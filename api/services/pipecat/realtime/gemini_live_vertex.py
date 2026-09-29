"""Dograh subclass of pipecat's Gemini Live Vertex AI LLM service.

Diamond inheritance: combines the Dograh engine-integration overrides from
:class:`DograhGeminiLiveLLMService` with the Vertex-specific tweaks from
upstream's :class:`GeminiLiveVertexLLMService` (no history config,
``NON_BLOCKING`` tools disabled, service-account credentials).

MRO::

    DograhGeminiLiveVertexLLMService
      -> DograhGeminiLiveLLMService
      -> GeminiLiveVertexLLMService
      -> GeminiLiveLLMService
      -> LLMService
      -> ...
"""

from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService
from pipecat.services.google.gemini_live.vertex.llm import (
    GeminiLiveVertexLLMService,
)


class DograhGeminiLiveVertexLLMService(
    DograhGeminiLiveLLMService,
    GeminiLiveVertexLLMService,
):
    """Vertex AI variant of Gemini Live with Dograh integration quirks."""

    @property
    def _supports_non_blocking_tools(self) -> bool:  # type: ignore[override]
        # Vertex Live endpoint does not support NON_BLOCKING; keep upstream
        # Vertex behavior even for 3.8 models.
        return False

    @property
    def _tools_default_to_non_blocking(self) -> bool:
        return False

    def _tag_tool_behaviors(self, tools: list) -> None:
        # No behavior tagging on Vertex; sending either field breaks tool
        # calling against the Vertex endpoint (upstream override).
        return


# Guard against MRO regressions: a future refactor that flips inheritance
# order or breaks the diamond would silently bypass the Dograh overrides.
_mro = DograhGeminiLiveVertexLLMService.__mro__
assert _mro[1] is DograhGeminiLiveLLMService, (
    f"Expected DograhGeminiLiveLLMService at MRO[1], got {_mro[1]}"
)
assert _mro[2] is GeminiLiveVertexLLMService, (
    f"Expected GeminiLiveVertexLLMService at MRO[2], got {_mro[2]}"
)
del _mro
