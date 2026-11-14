"""Queue consumer coordinating classification, extraction, clarification, and persistence."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from procure_agents.intent.clarifier import (
    format_merged_text,
    generate_clarifying_question,
)
from procure_agents.intent.classifier import IntentClassifier
from procure_agents.intent.extractor import IntakeExtractor
from procure_agents.llm.client import LLMClientProtocol, ResilientLLMClient
from procure_agents.models import ClassificationType
from procure_context.resolution.resolver import EntityResolutionService
from procure_core.config import Settings, get_settings
from procure_core.log_config import get_logger
from procure_core.models.intent import ProcurementIntentModel
from procure_core.models.request import RequestModel
from procure_core.models.vendor import IntentVendorResolutionModel
from procure_core.models.workspace import WorkspaceInstallationModel
from procure_ingress.models import InboundMessage, QueueItem
from procure_ingress.queue import PostgresInboundQueue
from procure_ingress.session import ConversationSessionManager
from workers.slack_outbound import SlackOutboundClient, SlackOutboundClientProtocol

logger = get_logger(__name__)


class IntakeConsumer:
    """Coordinates procurement intake parsing, clarification dialogue, and persistence."""

    def __init__(
        self,
        session: AsyncSession,
        llm_client: LLMClientProtocol | None = None,
        slack_client: SlackOutboundClientProtocol | None = None,
        settings: Settings | None = None,
        resolver: EntityResolutionService | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.llm = llm_client or ResilientLLMClient(self.settings)
        self.classifier = IntentClassifier(self.llm)
        self.extractor = IntakeExtractor(self.llm)
        self.slack = slack_client or SlackOutboundClient(self.settings.SLACK_BOT_TOKEN)
        self.queue = PostgresInboundQueue(session)
        self.session_manager = ConversationSessionManager(session)
        self.resolver = resolver

    async def get_workspace_bot_token(self, tenant_id: str) -> str:
        """Resolve bot token for outbound Slack messaging."""
        stmt = select(WorkspaceInstallationModel.bot_token).where(
            (WorkspaceInstallationModel.tenant_id == tenant_id)
            & (WorkspaceInstallationModel.platform == "slack")
        )
        result = await self.session.execute(stmt)
        token = result.scalar_one_or_none()
        return token or self.settings.SLACK_BOT_TOKEN

    async def process_queue_item(self, item: QueueItem) -> None:
        """Process a single enqueued inbound event."""
        if not item.normalized_payload:
            await self.queue.mark_failed(item.id, "Missing normalized payload")
            return

        message = InboundMessage.model_validate(item.normalized_payload)
        bot_token = await self.get_workspace_bot_token(message.tenant_id)

        # 1. Retrieve or initialize conversation session
        session_item = await self.session_manager.get_or_create_session(
            tenant_id=message.tenant_id,
            channel_type=message.channel.value,
            conversation_id=message.conversation_id,
            thread_id=message.thread_id,
        )

        # 2. Check for pending intent awaiting clarification in thread (with row lock)
        pending_stmt = (
            select(ProcurementIntentModel)
            .where(
                (ProcurementIntentModel.session_id == session_item.id)
                & (ProcurementIntentModel.status == "awaiting_clarification")
            )
            .with_for_update()
        )
        pending_result = await self.session.execute(pending_stmt)
        pending_intent = pending_result.scalar_one_or_none()

        if pending_intent is not None:
            # === CLARIFICATION FOLLOW-UP TURN ===
            await self._handle_clarification_turn(pending_intent, message, bot_token)
        else:
            # === INITIAL TURN ===
            await self._handle_initial_turn(session_item.id, message, bot_token)

        # 3. Mark queue item complete
        await self.queue.mark_complete(item.id)

    async def _handle_initial_turn(
        self,
        session_id: uuid.UUID,
        message: InboundMessage,
        bot_token: str,
    ) -> None:
        """Process first turn: classify intent, extract fields, or route accordingly."""
        # Step 1: Classify intent
        classification = await self.classifier.classify(message.text)

        # Route non-purchase queries
        if classification.classification == ClassificationType.STATUS_CHECK:
            reply = (
                f"Checking status for request {classification.request_ref}: Under review."
                if classification.request_ref
                else "Could you please provide the request reference number (e.g. #REQ-104)?"
            )
            await self.slack.post_message(
                channel=message.conversation_id,
                text=reply,
                thread_ts=message.thread_id,
                response_url=message.response_url,
                bot_token=bot_token,
            )
            return

        if classification.classification == ClassificationType.VENDOR_QUESTION:
            reply = (
                "We received your vendor query. A procurement specialist will follow up shortly."
            )
            await self.slack.post_message(
                channel=message.conversation_id,
                text=reply,
                thread_ts=message.thread_id,
                response_url=message.response_url,
                bot_token=bot_token,
            )
            return

        if classification.classification == ClassificationType.OTHER:
            reply = (
                "I'm your procurement assistant. Did you mean to request a new purchase, "
                "renew a contract, or check the status of an existing request?"
            )
            await self.slack.post_message(
                channel=message.conversation_id,
                text=reply,
                thread_ts=message.thread_id,
                response_url=message.response_url,
                bot_token=bot_token,
            )
            return

        # Step 2: New Purchase or Renewal -> Extract fields
        fields, spend, missing, evidence = await self.extractor.extract(message.text)

        now = datetime.now(UTC)
        expires_at = now + timedelta(days=7)

        if missing:
            # Generate EXACTLY ONE question covering all missing items
            question = (
                generate_clarifying_question(missing, fields.vendor_name)
                or "Could you clarify the budget?"
            )

            intent_record = ProcurementIntentModel(
                id=uuid.uuid4(),
                tenant_id=message.tenant_id,
                session_id=session_id,
                source_event_id=message.ordering_key,
                prompt_version=self.settings.PROMPT_VERSION,
                model=self.settings.LLM_MODEL,
                intent_type=classification.classification.value,
                status="awaiting_clarification",
                clarification_count=1,
                needs_review=spend.needs_review,
                expires_at=expires_at,
                vendor_name=fields.vendor_name,
                replaces_vendor=fields.replaces_vendor,
                candidate_vendors=fields.candidate_vendors,
                estimated_spend=spend.amount,
                annualized_spend=spend.annualized_spend,
                currency=spend.currency,
                billing_period=spend.billing_period.value,
                justification=fields.justification,
                department=fields.department,
                tags=fields.tags,
                evidence=evidence,
                missing_fields=missing,
                clarifying_question=question,
                raw_text=message.text,
            )
            self.session.add(intent_record)
            await self._resolve_vendors(intent_record)
            await self.session.flush()

            # Post the single clarifying question to Slack
            await self.slack.post_message(
                channel=message.conversation_id,
                text=question,
                thread_ts=message.thread_id,
                response_url=message.response_url,
                bot_token=bot_token,
            )
        else:
            # Complete extraction: create purchase request in requests table
            request_record = await self._create_procurement_request(
                tenant_id=message.tenant_id,
                requester_id=message.user_id,
                fields=fields,
                spend=spend,
            )

            intent_record = ProcurementIntentModel(
                id=uuid.uuid4(),
                tenant_id=message.tenant_id,
                session_id=session_id,
                request_id=request_record.id,
                source_event_id=message.ordering_key,
                prompt_version=self.settings.PROMPT_VERSION,
                model=self.settings.LLM_MODEL,
                intent_type=classification.classification.value,
                status="complete",
                clarification_count=0,
                needs_review=spend.needs_review,
                expires_at=expires_at,
                vendor_name=fields.vendor_name,
                replaces_vendor=fields.replaces_vendor,
                candidate_vendors=fields.candidate_vendors,
                estimated_spend=spend.amount,
                annualized_spend=spend.annualized_spend,
                currency=spend.currency,
                billing_period=spend.billing_period.value,
                justification=fields.justification,
                department=fields.department,
                tags=fields.tags,
                evidence=evidence,
                missing_fields=[],
                raw_text=message.text,
            )
            self.session.add(intent_record)
            await self.session_manager.link_request_to_session(session_id, request_record.id)
            await self._resolve_vendors(intent_record, request_record=request_record)
            await self.session.flush()

            confirmation = (
                f"✅ Procurement request submitted for *{fields.vendor_name}* "
                f"({spend.currency} {spend.amount:,.2f})! Request ID: `{request_record.id}`"
            )
            await self.slack.post_message(
                channel=message.conversation_id,
                text=confirmation,
                thread_ts=message.thread_id,
                response_url=message.response_url,
                bot_token=bot_token,
            )

    async def _handle_clarification_turn(
        self,
        pending_intent: ProcurementIntentModel,
        message: InboundMessage,
        bot_token: str,
    ) -> None:
        """Merge user reply with original text and re-extract."""
        merged_text = format_merged_text(pending_intent.raw_text, message.text)
        fields, spend, missing, evidence = await self.extractor.extract(merged_text)

        pending_intent.vendor_name = fields.vendor_name or pending_intent.vendor_name
        pending_intent.replaces_vendor = fields.replaces_vendor or pending_intent.replaces_vendor
        pending_intent.candidate_vendors = (
            fields.candidate_vendors or pending_intent.candidate_vendors
        )
        pending_intent.estimated_spend = (
            spend.amount if spend.amount is not None else pending_intent.estimated_spend
        )
        pending_intent.annualized_spend = (
            spend.annualized_spend
            if spend.annualized_spend is not None
            else pending_intent.annualized_spend
        )
        pending_intent.currency = spend.currency or pending_intent.currency
        pending_intent.billing_period = (
            spend.billing_period.value
            if spend.billing_period.value != "unknown"
            else pending_intent.billing_period
        )
        pending_intent.justification = fields.justification or pending_intent.justification
        pending_intent.department = fields.department or pending_intent.department
        pending_intent.evidence = evidence
        pending_intent.raw_text = merged_text

        # Re-check missing fields
        if missing:
            # Rule 6: Hand off to human if still missing fields. No second question!
            pending_intent.status = "needs_human_review"
            pending_intent.needs_review = True
            pending_intent.missing_fields = missing
            await self._resolve_vendors(pending_intent)
            await self.session.flush()

            hand_off_msg = (
                "Thanks! Some details are still unconfirmed. "
                "Our procurement team has been notified and will assist you directly."
            )
            await self.slack.post_message(
                channel=message.conversation_id,
                text=hand_off_msg,
                thread_ts=message.thread_id,
                response_url=message.response_url,
                bot_token=bot_token,
            )
        else:
            # Complete!
            request_record = await self._create_procurement_request(
                tenant_id=message.tenant_id,
                requester_id=message.user_id,
                fields=fields,
                spend=spend,
            )
            pending_intent.status = "complete"
            pending_intent.request_id = request_record.id
            pending_intent.missing_fields = []
            await self.session_manager.link_request_to_session(
                pending_intent.session_id, request_record.id
            )
            await self._resolve_vendors(pending_intent, request_record=request_record)
            await self.session.flush()

            confirmation = (
                f"✅ Details confirmed! Procurement request submitted for *{fields.vendor_name}* "
                f"({spend.currency} {spend.amount:,.2f})! Request ID: `{request_record.id}`"
            )
            await self.slack.post_message(
                channel=message.conversation_id,
                text=confirmation,
                thread_ts=message.thread_id,
                response_url=message.response_url,
                bot_token=bot_token,
            )

    async def _create_procurement_request(
        self,
        tenant_id: str,
        requester_id: str,
        fields: Any,
        spend: Any,
    ) -> RequestModel:
        """Persist a new procurement request in requests table upon complete extraction."""
        payload = {
            "vendor_name": fields.vendor_name,
            "replaces_vendor": fields.replaces_vendor,
            "candidate_vendors": fields.candidate_vendors,
            "estimated_spend": float(spend.amount)
            if isinstance(spend.amount, Decimal)
            else spend.amount,
            "annualized_spend": float(spend.annualized_spend)
            if isinstance(spend.annualized_spend, Decimal)
            else spend.annualized_spend,
            "currency": spend.currency,
            "billing_period": spend.billing_period.value,
            "justification": fields.justification,
            "department": fields.department,
            "tags": fields.tags,
        }

        req = RequestModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            requester_id=requester_id,
            status="draft",
            payload=payload,
        )
        self.session.add(req)
        await self.session.flush()
        return req

    async def _resolve_vendors(
        self,
        intent: ProcurementIntentModel,
        request_record: RequestModel | None = None,
    ) -> None:
        """Resolve vendor_name, replaces_vendor, candidate_vendors via EntityResolutionService."""
        resolver = self.resolver or EntityResolutionService(intent.tenant_id)

        # 1. Primary vendor_name
        if intent.vendor_name:
            res_primary = await resolver.resolve_transactional(
                self.session, intent.vendor_name, commit_if_auto=True
            )
            if res_primary.decision_id:
                ivr_stmt = (
                    pg_insert(IntentVendorResolutionModel)
                    .values(
                        tenant_id=intent.tenant_id,
                        intent_id=intent.id,
                        role="vendor_name",
                        source_name=intent.vendor_name,
                        vendor_id=res_primary.matched_vendor_id,
                        outcome=res_primary.outcome.lower(),
                        decision_id=res_primary.decision_id,
                    )
                    .on_conflict_do_update(
                        constraint="uq_intent_vendor_resolutions",
                        set_={
                            "vendor_id": res_primary.matched_vendor_id,
                            "outcome": res_primary.outcome.lower(),
                            "decision_id": res_primary.decision_id,
                        },
                    )
                )
                await self.session.execute(ivr_stmt)

            if res_primary.needs_review:
                intent.needs_review = True

            if res_primary.matched_vendor_id and request_record is not None:
                request_record.vendor_id = res_primary.matched_vendor_id

        # 2. replaces_vendor
        if intent.replaces_vendor:
            res_rep = await resolver.resolve_transactional(
                self.session, intent.replaces_vendor, commit_if_auto=True
            )
            if res_rep.decision_id:
                ivr_rep_stmt = (
                    pg_insert(IntentVendorResolutionModel)
                    .values(
                        tenant_id=intent.tenant_id,
                        intent_id=intent.id,
                        role="replaces_vendor",
                        source_name=intent.replaces_vendor,
                        vendor_id=res_rep.matched_vendor_id,
                        outcome=res_rep.outcome.lower(),
                        decision_id=res_rep.decision_id,
                    )
                    .on_conflict_do_update(
                        constraint="uq_intent_vendor_resolutions",
                        set_={
                            "vendor_id": res_rep.matched_vendor_id,
                            "outcome": res_rep.outcome.lower(),
                            "decision_id": res_rep.decision_id,
                        },
                    )
                )
                await self.session.execute(ivr_rep_stmt)

        # 3. candidate_vendors
        if intent.candidate_vendors:
            for cand in intent.candidate_vendors:
                cand_name = str(cand).strip()
                if not cand_name:
                    continue
                res_cand = await resolver.resolve_transactional(
                    self.session, cand_name, commit_if_auto=True
                )
                if res_cand.decision_id:
                    ivr_cand_stmt = (
                        pg_insert(IntentVendorResolutionModel)
                        .values(
                            tenant_id=intent.tenant_id,
                            intent_id=intent.id,
                            role="candidate_vendor",
                            source_name=cand_name,
                            vendor_id=res_cand.matched_vendor_id,
                            outcome=res_cand.outcome.lower(),
                            decision_id=res_cand.decision_id,
                        )
                        .on_conflict_do_update(
                            constraint="uq_intent_vendor_resolutions",
                            set_={
                                "vendor_id": res_cand.matched_vendor_id,
                                "outcome": res_cand.outcome.lower(),
                                "decision_id": res_cand.decision_id,
                            },
                        )
                    )
                    await self.session.execute(ivr_cand_stmt)
