"""Policy drafting agent and deterministic anti-weakening policy validator."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from procure_agents.approval.models import ApprovalPolicyRules
from procure_agents.llm.client import LLMClientProtocol, ResilientLLMClient
from procure_core.config import Settings, get_settings


class PolicyDiff(BaseModel):
    """Structured diff representation between policy versions."""

    model_config = ConfigDict(extra="forbid")

    added: dict[str, Any] = Field(default_factory=dict)
    modified: dict[str, Any] = Field(default_factory=dict)
    removed: dict[str, Any] = Field(default_factory=dict)


class PolicyValidationResult(BaseModel):
    """Result of deterministic policy validation and anti-weakening checks."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    reasons: list[str] = Field(default_factory=list)
    diff: dict[str, Any] = Field(default_factory=dict)
    validated_rules: dict[str, Any] | None = None


class PolicyDraftResult(BaseModel):
    """Result of LLM policy drafting including validation and diff."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    proposed_rules: dict[str, Any] | None = None
    diff: dict[str, Any] = Field(default_factory=dict)
    validation: PolicyValidationResult | None = None
    error: str | None = None


class DeterministicPolicyValidator:
    """Enforces strict schema compliance and anti-weakening invariants on policy rules."""

    HARD_AUTONOMOUS_CAP = Decimal("100000.00")

    @classmethod
    def compute_diff(
        cls,
        existing: dict[str, Any] | ApprovalPolicyRules | None,
        candidate: dict[str, Any] | ApprovalPolicyRules | None,
    ) -> dict[str, Any]:
        """Compute structured diff (added, modified, removed) between two rule sets."""

        def _to_clean_dict(obj: Any) -> dict[str, Any]:
            if isinstance(obj, ApprovalPolicyRules):
                d = obj.model_dump()
            elif isinstance(obj, dict):
                d = dict(obj)
            else:
                return {}

            def _clean(val: Any) -> Any:
                if isinstance(val, Decimal):
                    return float(val)
                if isinstance(val, dict):
                    return {k: _clean(v) for k, v in val.items()}
                if isinstance(val, list):
                    return [_clean(x) for x in val]
                return val

            return {k: _clean(v) for k, v in d.items()}

        old_dict = _to_clean_dict(existing)
        new_dict = _to_clean_dict(candidate)

        added = {k: v for k, v in new_dict.items() if k not in old_dict}
        removed = {k: v for k, v in old_dict.items() if k not in new_dict}
        modified: dict[str, Any] = {}

        for k in old_dict:
            if k in new_dict and old_dict[k] != new_dict[k]:
                modified[k] = {"old": old_dict[k], "new": new_dict[k]}

        return {"added": added, "modified": modified, "removed": removed}

    def validate(
        self,
        candidate_data: dict[str, Any] | ApprovalPolicyRules,
        existing_rules: dict[str, Any] | ApprovalPolicyRules | None = None,
        admin_override: bool = False,
    ) -> PolicyValidationResult:
        """Validate candidate policy rules against schema and anti-weakening invariants."""
        # 1. Schema Validation (extra='forbid')
        candidate: ApprovalPolicyRules
        if isinstance(candidate_data, ApprovalPolicyRules):
            candidate = candidate_data
        else:
            try:
                candidate = ApprovalPolicyRules.model_validate(candidate_data)
            except ValidationError as exc:
                err_reasons = [
                    f"Schema violation: {'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                    for err in exc.errors()
                ]
                diff = self.compute_diff(existing_rules, candidate_data)
                return PolicyValidationResult(
                    valid=False,
                    reasons=err_reasons,
                    diff=diff,
                    validated_rules=None,
                )

        # 2. Parse existing rules if provided
        existing: ApprovalPolicyRules | None = None
        if isinstance(existing_rules, ApprovalPolicyRules):
            existing = existing_rules
        elif isinstance(existing_rules, dict):
            try:
                existing = ApprovalPolicyRules.model_validate(existing_rules)
            except Exception:
                existing = None

        reasons: list[str] = []

        # 3. Anti-weakening checks
        # Check A: agent_autonomous_cap hard ceiling and monotonic increase check
        if candidate.agent_autonomous_cap > self.HARD_AUTONOMOUS_CAP:
            reasons.append(
                f"agent_autonomous_cap ({candidate.agent_autonomous_cap}) exceeds hard ceiling of {self.HARD_AUTONOMOUS_CAP} USD"
            )
        elif (
            existing is not None
            and candidate.agent_autonomous_cap > existing.agent_autonomous_cap
            and not admin_override
        ):
            reasons.append(
                f"agent_autonomous_cap cannot be increased from {existing.agent_autonomous_cap} to {candidate.agent_autonomous_cap} without explicit admin override"
            )

        # Check B: vp_spend_threshold must not be empty, must be valid, and cannot be weakened
        if not candidate.vp_spend_threshold or len(candidate.vp_spend_threshold) == 0:
            reasons.append("vp_spend_threshold must NOT be empty")
        else:
            for curr, thresh in candidate.vp_spend_threshold.items():
                if not isinstance(curr, str) or len(curr) != 3 or not curr.isupper():
                    reasons.append(f"Invalid currency code '{curr}' in vp_spend_threshold")
                if thresh <= Decimal("0"):
                    reasons.append(
                        f"vp_spend_threshold for currency '{curr}' must be greater than zero"
                    )

            if existing is not None:
                for old_curr, old_thresh in existing.vp_spend_threshold.items():
                    if old_curr not in candidate.vp_spend_threshold and not admin_override:
                        reasons.append(
                            f"Existing threshold currency '{old_curr}' cannot be deleted without explicit human confirmation"
                        )
                    elif (
                        old_curr in candidate.vp_spend_threshold
                        and candidate.vp_spend_threshold[old_curr] > old_thresh
                        and not admin_override
                    ):
                        reasons.append(
                            f"vp_spend_threshold for currency '{old_curr}' cannot be weakened (increased from {old_thresh} to {candidate.vp_spend_threshold[old_curr]}) without explicit admin override"
                        )

        # Check C: over_budget_action must be 'needs_human' or 'auto_reject'
        if candidate.over_budget_action not in ("needs_human", "auto_reject"):
            reasons.append("over_budget_action must be 'needs_human' or 'auto_reject'")

        # Check D: fast_track_consecutive_cap cannot exceed 3 if fast track is allowed
        if candidate.allow_fast_track and candidate.fast_track_consecutive_cap > 3:
            reasons.append("fast_track_consecutive_cap cannot exceed 3 when fast track is allowed")

        diff = self.compute_diff(existing, candidate)

        if reasons:
            return PolicyValidationResult(
                valid=False,
                reasons=reasons,
                diff=diff,
                validated_rules=None,
            )

        validated_dict = json.loads(candidate.model_dump_json())
        return PolicyValidationResult(
            valid=True,
            reasons=[],
            diff=diff,
            validated_rules=validated_dict,
        )


class PolicyDraftingAgent:
    """Agent that translates plain-English policy rules into schema-validated ApprovalPolicyRules."""

    def __init__(
        self,
        llm_client: LLMClientProtocol | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm_client
        self.validator = DeterministicPolicyValidator()

    async def draft_policy(
        self,
        description: str,
        existing_rules: dict[str, Any] | ApprovalPolicyRules | None = None,
        admin_override: bool = False,
    ) -> PolicyDraftResult:
        """Draft candidate policy rules using guarded LLM client and validate deterministically."""
        if not self.settings.LLM_API_KEY or not self.settings.LLM_API_KEY.strip():
            return PolicyDraftResult(
                success=False,
                error="LLM provider is not configured. Please configure LLM_API_KEY.",
            )

        client = self.llm or ResilientLLMClient(self.settings)

        system_prompt = (
            "You are an enterprise procurement policy drafting agent.\n"
            "Given an administrator's plain-English rule description and any existing policy rules,\n"
            "generate an updated candidate ApprovalPolicyRules object.\n"
            "Constraints:\n"
            "- vp_spend_threshold must map ISO currency codes (USD, EUR, GBP, CAD) to positive amounts.\n"
            "- agent_autonomous_cap cannot exceed 100000.00.\n"
            "- over_budget_action must be 'needs_human' or 'auto_reject'.\n"
            "- fast_track_consecutive_cap cannot exceed 3.\n"
            "Return structured JSON matching ApprovalPolicyRules."
        )

        existing_dict: dict[str, Any] = {}
        if isinstance(existing_rules, ApprovalPolicyRules):
            existing_dict = json.loads(existing_rules.model_dump_json())
        elif isinstance(existing_rules, dict):
            existing_dict = existing_rules

        user_content = (
            f"Existing active policy rules:\n{json.dumps(existing_dict, indent=2)}\n\n"
            f"Admin instruction:\n{description}"
        )

        try:
            candidate_model: ApprovalPolicyRules = await client.call_structured(
                system_prompt=system_prompt,
                user_content=user_content,
                response_model=ApprovalPolicyRules,
                temperature=0.0,
            )
            candidate_dict = json.loads(candidate_model.model_dump_json())
        except Exception as exc:
            return PolicyDraftResult(
                success=False,
                error=f"LLM drafting failed: {exc}",
            )

        val_result = self.validator.validate(
            candidate_data=candidate_dict,
            existing_rules=existing_rules,
            admin_override=admin_override,
        )

        return PolicyDraftResult(
            success=val_result.valid,
            proposed_rules=candidate_dict,
            diff=val_result.diff,
            validation=val_result,
            error=None if val_result.valid else "; ".join(val_result.reasons),
        )
