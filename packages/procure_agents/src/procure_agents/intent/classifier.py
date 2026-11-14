"""Intent classifier categorizing inbound messages into 5 standard procurement intents."""

from pathlib import Path

from procure_agents.llm.client import LLMClientProtocol, ResilientLLMClient
from procure_agents.models import ClassificationType, IntentClassification


def load_classify_prompt() -> str:
    """Load the versioned classification system prompt."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "classify_v1.txt"
    return prompt_path.read_text(encoding="utf-8")


class IntentClassifier:
    """Classifies user intent before entering procurement extraction."""

    def __init__(self, llm_client: LLMClientProtocol | None = None) -> None:
        self.llm = llm_client or ResilientLLMClient()
        self.system_prompt = load_classify_prompt()

    async def classify(self, text: str) -> IntentClassification:
        """Classify message text into one of 5 categories."""
        result: IntentClassification = await self.llm.call_structured(
            system_prompt=self.system_prompt,
            user_content=text,
            response_model=IntentClassification,
            temperature=0.0,
        )

        # Rule 9: Low confidence (< 0.70) routes to 'other'
        if result.confidence < 0.70 and result.classification != ClassificationType.OTHER:
            return IntentClassification(
                classification=ClassificationType.OTHER,
                confidence=result.confidence,
                request_ref=result.request_ref,
                reasoning=(
                    f"Low classification confidence ({result.confidence:.2f}); routing to other."
                ),
            )

        return result
