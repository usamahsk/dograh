# Gemini 3.8 Live integration: handoff guide

This guide covers every code change, test and setup step needed to run **Gemini 3.8 Live** (`gemini-3.8-live`) as a realtime voice model on another Dograh deployment. It mirrors what runs in production on the Botrix fork (branch `botrix-main`).

### Every change we made, in the order to apply them

The 3.8 service **extends** our `DograhGeminiLiveLLMService`. That class already carries two earlier Gemini Live changes (session refresh/compaction and the reconnect race fix). A setup without them won't behave like ours, so they are part of this handoff too.

| # | Commit | What it changed | Files | Required? | Where in this guide |
|---|---|---|---|---|---|
| 1 | `02d1075a` | Pin `google-genai==2.24.0` and `uv:0.12.21`; add a test that fails if the audio wire format changes | `api/requirements.txt`, `api/Dockerfile`, `api/tests/test_gemini_live_audio_wire_format.py` | **Yes** | 3.1, Appendix D |
| 2 | `d2f5b40d` | Pin **every** Python package via `api/constraints.txt`, used by both install steps | `api/constraints.txt` (new), `api/Dockerfile` | **Yes** | 3.1, Appendices D and E |
| 3 | `83ebf437` | Let `.env` set the api's `LOG_LEVEL` (was hard-coded `INFO`) | `docker-compose.yaml` | Recommended (needed for DEBUG checks) | 3.2, Appendix C |
| 4 | `b4315db7` | Gemini Live **session refresh / context compaction**, and Google's server-side compression wiring | `gemini_live.py`, `service_factory.py`, `api/tests/test_gemini_live_context_compaction.py` | **Yes** (the 3.8 class inherits it; cuts cost ~39–56%) | Appendices A, B and F |
| 5 | `4721ae7c` | Pass the two Gemini Live cost env vars through compose to the api | `docker-compose.yaml` | **Yes** if using compaction | 3.2, Appendix C |
| 6 | `a66dd0ba` | Fix the reconnect race (`'NoneType' object has no attribute 'receive'`, "failed after 3 consecutive attempts") | `gemini_live.py`, `api/tests/test_gemini_live_reconnect_race.py` | **Yes** | Appendices A and F |
| 7 | `47a96244` | The isolated **Gemini 3.8 Live** provider, plus the node-transition "deafness" guard (upstream `fa1df112`) | 9 files (section 4) | **Yes** | Section 5 |
| 8 | `c535bd4a` | Gemini Live opens silently after a *recorded* greeting (upstream `9c82ceb0`) | `audio_playback.py`, `gemini_live.py`, `pipecat_engine.py`, test | Recommended | Section 6 |

Changes `02193def` and its revert `fb18088b` cancel each other out. **Don't apply either.**

**Two ways to apply all of this:**

- **Fastest:** use the patch files in [`handoff/patches/`](patches/), one per commit, in the order above. Apply them with `git am` (section 15).
- **By hand:** replace `api/services/pipecat/realtime/gemini_live.py` with the complete final file in **Appendix A**. That single file contains changes 4, 6, the guard from 7, and the gemini_live part of 8. Then follow sections 3, 5 and 6 for the other files.

---

## 1. Why a separate provider is needed

Gemini 3.8 Live changed how it runs function calls (tools):

- **On 3.8, tools are `NON_BLOCKING` unless the declaration says `BLOCKING`.** A non-blocking tool runs in the background while the model keeps talking, so the model answers *before the result arrives*.
- The pipecat that Dograh pins (submodule `dograh-hq/pipecat` at `aadd1d5dd606d2871b082e6f2ca1ad1eee53785b`, the v1.5.0 merge) **never declares `BLOCKING`**. It predates 3.8.
- **What we saw:** on a Vectus call, choosing `gemini-3.8-live` on the normal "Google Realtime" provider made the agent *invent* the Area Manager's name and number instead of waiting for `vectus_area_manager_lookup`. Sometimes it didn't call the tool at all.
- Pipecat **v1.12** fixes this with `GeminiLiveLLMService._tag_tool_behaviors` and a reworked `_tool_result`. Upgrading pipecat for the whole app is a large merge, so we **backported just those two pieces** into a subclass. The subclass is used only by a new provider.

**Isolation guarantee:** the existing **Google Realtime** provider (Gemini 3.1, `gemini-3.1-flash-live-preview`) and its service class `DograhGeminiLiveLLMService` keep their behaviour. A test proves it (section 8). The node-transition guard (section 5.9) and the recorded-greeting fix (section 6) do change the shared class, but they are bug fixes that help both models.

### What the 3.8 service changes

1. **Declares tool behaviour.** Just before the Live session opens, each synchronous tool is tagged `"behavior": "BLOCKING"`. Tools registered with `cancel_on_interruption=False` (async or background tools) are tagged `"NON_BLOCKING"`.
2. **Adds `WHEN_IDLE` for async results.** When an async tool returns, its result carries `"scheduling": "WHEN_IDLE"`, so the model mentions it at a natural pause.
3. **Drops the old 3.x nudge.** Pipecat at the pinned version sends `send_realtime_input(text=" ")` after every tool result, because older 3.x needed it to continue. 3.8 continues on its own, and the extra input can read as a new user turn. The 3.8 service doesn't send it.
4. **Reports `_supports_non_blocking_tools = True`.** The pinned parent returns `False` for any gemini-3 model.

### Not supported

The **extended-thinking** 3.8 variant runs *every* tool `NON_BLOCKING` and reports `interaction_status`, which needs pipecat v1.12's turn handling. Only plain `gemini-3.8-live` is supported.

---

## 2. Decide which path you need

| Target deployment | What to do |
|---|---|
| Its pipecat is **≥ v1.12** (check `pipecat/` submodule or `pip show pipecat-ai`; see the caveat below) | **No backport needed.** Pipecat already tags behaviours. Choose the normal Google Realtime provider and set the model to `gemini-3.8-live`. Still apply section 5.9 (guard) and section 6 (greeting) if they aren't in its Dograh version yet. |
| Its pipecat is **older than v1.12** (like ours, the v1.5 merge) | Apply **all** of section 5. This is the main path in this guide. |

**Checking the pipecat version:** the version pipecat *prints* can be stale package metadata. Check the code itself instead:

```bash
grep -n "_tag_tool_behaviors" pipecat/src/pipecat/services/google/gemini_live/llm.py
# a match means v1.12+ behaviour is present, so the backport is not needed
```

---

## 3. Prerequisites in the target codebase

The backport subclasses Dograh's own `DograhGeminiLiveLLMService` and relies on attributes from the pinned pipecat `GeminiLiveLLMService`. Check that they all exist before copying code:

```bash
# Dograh wrapper class (must exist, with a _connection_task_handler override)
grep -n "class DograhGeminiLiveLLMService\|async def _connection_task_handler\|COMPRESSION_TRIGGER_TOKENS =" \
  api/services/pipecat/realtime/gemini_live.py

# pipecat base attributes the 3.8 class uses
grep -n "_supports_non_blocking_tools\|def _function_is_async\|_pending_tool_results\|def _handle_send_error\|def _tool_result\|_disconnecting" \
  pipecat/src/pipecat/services/google/gemini_live/llm.py
```

You should find:

- `DograhGeminiLiveLLMService` in `api/services/pipecat/realtime/gemini_live.py`.
- The module-level constant `COMPRESSION_TRIGGER_TOKENS` in the same file. It reads the env var `GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS` (default `16000`).
- In pipecat's `GeminiLiveLLMService`:
  - the property `_supports_non_blocking_tools`;
  - the methods `_function_is_async(name)`, `_handle_send_error(e)` and `_tool_result(...)`;
  - the attributes `_pending_tool_results` (a dict) and `_disconnecting` (a bool).

If any are missing, the target's pipecat is older than v1.5. Upgrade it to at least the v1.5 merge first.

### 3.1 Dependency pins (do this first)

On 2026-09-30 a routine rebuild pulled in **`google-genai` 2.25.0**, which sends `mime_type` in realtime audio. That broke Gemini Live speech recognition: the bot greeted and then heard nothing. To prevent this kind of drift:

1. **`api/requirements.txt`** must pin:
   ```
   google-genai==2.24.0
   ```
2. **`api/constraints.txt`** pins every installed package (about 193) to tested versions. Copy it from this repo. Then add `-c` to **both** `uv pip install` steps in `api/Dockerfile`:
   ```dockerfile
   # Pinned uv: an unpinned uv also resolves packages differently over time.
   COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /uvx /usr/local/bin/

   # Layer 1: API deps.
   # api/constraints.txt pins every package (pipecat's too) to a tested version,
   # so a rebuild can't silently upgrade anything.
   RUN --mount=type=bind,source=api/requirements.txt,target=/tmp/req.txt \
       --mount=type=bind,source=api/constraints.txt,target=/tmp/constraints.txt \
       --mount=type=cache,target=/root/.cache/uv \
       uv pip install -r /tmp/req.txt -c /tmp/constraints.txt

   # Layer 2: pipecat deps (same constraints file).
   RUN --mount=type=bind,source=pipecat,target=/tmp/pipecat,rw \
       --mount=type=bind,source=api/constraints.txt,target=/tmp/constraints.txt \
       --mount=type=cache,target=/root/.cache/uv \
       uv pip install '/tmp/pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb,mcp,inworld,smallest]' -c /tmp/constraints.txt \
    && uv pip uninstall opencv-python \
    && uv pip install opencv-python-headless -c /tmp/constraints.txt \
    && python -c "import nltk; nltk.download('punkt_tab', download_dir='/opt/venv/nltk_data', quiet=True)"
   ```
   This is copied from our `api/Dockerfile`. Only the two `constraints.txt` mounts, the `-c /tmp/constraints.txt` flags and the uv version are new; the rest is already in Dograh's Dockerfile.
   Keep the target's own pipecat extras list. The point is the `-c /tmp/constraints.txt` flag and the bind mount.

   > If the target's other packages differ, regenerate `constraints.txt` from a **known-good** running container (`docker compose exec api pip freeze`). Don't copy ours blindly.

### 3.2 Environment variables (all optional)

They go in `.env`. **Docker Compose only passes a variable to the api container if it's listed under the `api` service's `environment:` block.** Adding it to `.env` alone does nothing.

```yaml
# docker-compose.yaml, api service -> environment:
      LOG_LEVEL: "${LOG_LEVEL:-INFO}"
      GEMINI_LIVE_CONTEXT_COMPACTION_TRIGGER_TOKENS: "${GEMINI_LIVE_CONTEXT_COMPACTION_TRIGGER_TOKENS:-0}"
      GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS: "${GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS:-16000}"
```

| Variable | Meaning | Our prod value |
|---|---|---|
| `GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS` | Google's own server-side context-window compression. `0` turns it off. | default `16000` |
| `GEMINI_LIVE_CONTEXT_COMPACTION_TRIGGER_TOKENS` | Dograh's own compaction of the seeded history on reconnects. `0` turns it off. | `5000` |
| `LOG_LEVEL` | Set to `DEBUG` temporarily to see the `declared behavior on N Gemini 3.8 tool(s)` line (section 10). | `INFO` |

The 3.8 provider reads the **same** variables as the 3.1 provider; there is nothing 3.8-specific.

---

## 4. File overview

| # | File | Change |
|---|---|---|
| 1 | `api/services/pipecat/realtime/gemini_live_38.py` | **New file**: the 3.8 service class |
| 2 | `api/services/configuration/options/google.py` | add the `GEMINI_LIVE_38_MODELS` constant |
| 3 | `api/services/configuration/options/__init__.py` | export it |
| 4 | `api/services/configuration/registry.py` | enum value, provider list, model config, config class, `REALTIME_PROVIDERS`, `RealtimeConfig` union |
| 5 | `api/services/configuration/check_validity.py` | API-key check for the new provider |
| 6 | `api/services/pipecat/service_factory.py` | build the 3.8 service for the new provider |
| 7 | `api/services/pipecat/run_pipeline.py` | give it Gemini's VAD / turn configuration |
| 8 | `api/services/integrations/tuner/runtime.py` | label it as a realtime model in Tuner |
| 9 | `api/services/pipecat/realtime/gemini_live.py` | node-transition guard (shared, a bug fix) |
| 10 | `api/tests/test_gemini_live_38.py` | **New** tests |
| 11 | `api/tests/test_gemini_live_reconnect_tool_results.py` | regression test for the guard |
| - | **UI** | **No changes.** The model settings page renders providers from the backend schema, so "Gemini 3.8 Live" appears by itself. |
| - | **DB migrations** | **None.** The provider is stored in the existing JSON user configuration. |

---

## 5. Code changes, step by step

### 5.1 New file: `api/services/pipecat/realtime/gemini_live_38.py`

Create it with exactly this content:

```python
"""Gemini 3.8 Live, kept apart from the Gemini 3.1 path.

The 3.8 Live family changed how it runs tools, and the pipecat we ship
(dograh-hq/pipecat at the v1.5 merge) predates that. Rather than upgrade
pipecat for the whole app, this service carries the 3.8-specific behaviour,
ported from pipecat v1.12 (``GeminiLiveLLMService._tag_tool_behaviors`` and
``_tool_result``), and is selected only through its own provider
(``ServiceProviders.GEMINI_LIVE_38``). The Gemini 3.1 provider and
``DograhGeminiLiveLLMService`` are untouched.

What differs on 3.8:

- **Tools default to NON_BLOCKING.** Without an explicit ``BLOCKING``
  declaration, 3.8 keeps talking while a tool runs and answers before the
  result lands — on a Vectus call it invented the Area Manager instead of
  waiting for ``vectus_area_manager_lookup``. Synchronous tools are declared
  ``BLOCKING``; tools registered with ``cancel_on_interruption=False`` stay
  ``NON_BLOCKING`` (with the ``WHEN_IDLE`` scheduling hint on their results).
- **No nudge after a tool result.** Older 3.x needed a blank realtime-input
  nudge to continue after a tool response; 3.8 continues on its own, and the
  extra input can read as a new user turn.

Only plain ``gemini-3.8-live`` is supported here. The extended-thinking
variant runs every tool NON_BLOCKING and reports ``interaction_status``, which
needs pipecat v1.12's turn handling; use it only after that upgrade.
"""

from __future__ import annotations

from typing import Any

from google.genai.types import FunctionResponse
from loguru import logger

from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService

DEFAULT_MODEL = "gemini-3.8-live"


def tag_tool_behaviors(tools: Any, is_async) -> int:
    """Declare each function ``BLOCKING`` or ``NON_BLOCKING`` for 3.8.

    ``is_async(name)`` says whether the tool was registered with
    ``cancel_on_interruption=False``. Returns how many declarations were
    tagged. Mutates ``tools`` (Gemini's dict form) in place.
    """
    tagged = 0
    if not isinstance(tools, list):
        return tagged
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        decls = tool.get("function_declarations")
        if not isinstance(decls, list):
            continue
        for decl in decls:
            if not isinstance(decl, dict):
                continue
            name = decl.get("name")
            if not isinstance(name, str):
                continue
            decl["behavior"] = "NON_BLOCKING" if is_async(name) else "BLOCKING"
            tagged += 1
    return tagged


class DograhGemini38LiveLLMService(DograhGeminiLiveLLMService):
    """``DograhGeminiLiveLLMService`` with Gemini 3.8 Live tool semantics."""

    @property
    def _supports_non_blocking_tools(self) -> bool:
        # 3.8 supports NON_BLOCKING declarations and the scheduling hint on
        # results (older 3.x did not, which is what the parent assumes for any
        # gemini-3 model).
        return True

    async def _connection_task_handler(self, config):
        # The finished LiveConnectConfig, just before the session opens: make
        # every synchronous tool BLOCKING, since 3.8 would otherwise run it in
        # the background and answer without the result.
        tagged = tag_tool_behaviors(getattr(config, "tools", None), self._function_is_async)
        if tagged:
            logger.debug(f"{self}: declared behavior on {tagged} Gemini 3.8 tool(s)")
        return await super()._connection_task_handler(config)

    async def _tool_result(
        self, tool_call_id: str, tool_name: str, tool_result_message: dict[str, Any]
    ) -> bool:
        """Send a tool result, without the older-3.x continuation nudge.

        Same as pipecat's ``_tool_result`` at the pinned version, minus the
        trailing ``send_realtime_input(text=" ")``, matching pipecat v1.12.
        """
        if self._disconnecting or not self._session:
            logger.debug(
                f"{self}: queueing tool result for tool_call_id={tool_call_id} until session is ready"
            )
            self._pending_tool_results[tool_call_id] = (tool_name, tool_result_message)
            return False

        if self._function_is_async(tool_name):
            response_payload = {**tool_result_message, "scheduling": "WHEN_IDLE"}
        else:
            response_payload = tool_result_message

        response = FunctionResponse(name=tool_name, id=tool_call_id, response=response_payload)
        try:
            await self._session.send_tool_response(function_responses=response)
        except Exception as e:
            self._pending_tool_results[tool_call_id] = (tool_name, tool_result_message)
            await self._handle_send_error(e)
            return False

        self._pending_tool_results.pop(tool_call_id, None)
        return True
```

**How it works:**

- `_connection_task_handler(config)` is the last hook before pipecat opens the websocket. `config` is the finished `LiveConnectConfig`, and `config.tools` is a list of dicts like `{"function_declarations": [{"name": ..., "parameters": ...}]}`. We add a `behavior` key to each declaration in place, then let the parent (Dograh's wrapper, then pipecat) carry on.
- `_function_is_async(name)` is pipecat's own check: it returns True for a function registered with `cancel_on_interruption=False`.
- `_tool_result` copies pipecat's version at the pinned commit. It queues the result while reconnecting, adds `WHEN_IDLE` for async tools, sends the result, and requeues it if the send fails. It **drops** the trailing `send_realtime_input(text=" ")`.

If the target's pipecat `_tool_result` differs (for example, it returns something other than `bool`), keep its structure and copy only the two behavioural differences: no trailing nudge, and `WHEN_IDLE` without the `_supports_non_blocking_tools` gate.

### 5.2 `api/services/configuration/options/google.py`

Directly after the `GOOGLE_REALTIME_MODELS` line, add:

```python
GOOGLE_REALTIME_MODELS = ("gemini-3.1-flash-live-preview",)
# Gemini 3.8 Live has its own provider (see GeminiLive38LLMConfiguration):
# its tool semantics differ from 3.1's. Plain 3.8 only; the extended-thinking
# variant needs pipecat v1.12.
GEMINI_LIVE_38_MODELS = ("gemini-3.8-live",)
```

### 5.3 `api/services/configuration/options/__init__.py`

Add `GEMINI_LIVE_38_MODELS` to the import from `.google` and to `__all__`:

```python
from .google import (
    ...
    GOOGLE_REALTIME_MODELS,
    GEMINI_LIVE_38_MODELS,      # <- add
    GOOGLE_REALTIME_VOICES,
    ...
)

__all__ = [
    ...
    "GOOGLE_REALTIME_MODELS",
    "GEMINI_LIVE_38_MODELS",    # <- add
    "GOOGLE_REALTIME_VOICES",
    ...
]
```

### 5.4 `api/services/configuration/registry.py`

There are seven edits, all in this file.

**(a) Import the constant**:

```python
from api.services.configuration.options import (
    ...
    GOOGLE_REALTIME_MODELS,
    GEMINI_LIVE_38_MODELS,      # <- add
    GOOGLE_REALTIME_VOICES,
    ...
)
```

**(b) Add the enum value** to `class ServiceProviders(str, Enum)`, after `GOOGLE_REALTIME`:

```python
    GOOGLE_REALTIME = "google_realtime"
    GEMINI_LIVE_38 = "gemini_live_38"          # <- add
    GOOGLE_VERTEX_REALTIME = "google_vertex_realtime"
```

> The string `"gemini_live_38"` is what gets stored in users' saved configurations and in `workflow_runs.initial_context`. **Keep it identical** on every deployment, so configurations and reports stay portable.

**(c) Add it to the allowed provider list** in `class BaseServiceConfiguration(BaseModel)`:

```python
        ServiceProviders.GOOGLE_REALTIME,
        ServiceProviders.GEMINI_LIVE_38,       # <- add
        ServiceProviders.GOOGLE_VERTEX_REALTIME,
```

**(d) Add the provider's display config** next to the other `*_PROVIDER_MODEL_CONFIG` constants:

```python
GOOGLE_REALTIME_PROVIDER_MODEL_CONFIG = provider_model_config("Google Realtime")
GEMINI_LIVE_38_PROVIDER_MODEL_CONFIG = provider_model_config(
    "Gemini 3.8 Live",
    description=(
        "Gemini 3.8 Live on Google AI Studio, with 3.8's tool behaviour: tools "
        "are declared blocking so the agent waits for their results. Uses the "
        "same Google API key as Google Realtime."
    ),
)
```

If the target's `provider_model_config` doesn't accept `description=`, call it with just the display name.

**(e) Add the configuration class** directly after `class GoogleRealtimeLLMConfiguration`:

```python
@register_service(ServiceType.REALTIME)
class GeminiLive38LLMConfiguration(BaseLLMConfiguration):
    model_config = GEMINI_LIVE_38_PROVIDER_MODEL_CONFIG
    provider: Literal[ServiceProviders.GEMINI_LIVE_38] = ServiceProviders.GEMINI_LIVE_38
    model: str = Field(
        default="gemini-3.8-live",
        description="Gemini 3.8 Live model on Google AI Studio (not Vertex).",
        json_schema_extra={
            "examples": GEMINI_LIVE_38_MODELS,
            "allow_custom_input": True,
        },
    )
    voice: str = Field(
        default="Puck",
        description="Voice the model speaks in.",
        json_schema_extra={
            "examples": GOOGLE_REALTIME_VOICES,
            "allow_custom_input": True,
        },
    )
    language: str = Field(
        default="en",
        description="ISO 639-1 language code.",
        json_schema_extra={
            "examples": GOOGLE_REALTIME_LANGUAGES,
            "allow_custom_input": True,
        },
    )
```

`@register_service(ServiceType.REALTIME)` is what makes the provider appear in the UI's realtime model dropdown. `api_key` is inherited from `BaseLLMConfiguration`.

**(f) Add it to `REALTIME_PROVIDERS`**:

```python
REALTIME_PROVIDERS = {
    ...
    ServiceProviders.GOOGLE_REALTIME.value,
    ServiceProviders.GEMINI_LIVE_38.value,     # <- add
    ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
    ...
}
```

**(g) Add it to the `RealtimeConfig` discriminated union**:

```python
RealtimeConfig = Annotated[
    Union[
        ...
        GoogleRealtimeLLMConfiguration,
        GeminiLive38LLMConfiguration,          # <- add
        GoogleVertexRealtimeLLMConfiguration,
        ...
    ],
    ...
]
```

Skipping (g) makes saving a configuration with this provider fail validation. Skipping (f) makes the pipeline treat it as a non-realtime LLM.

### 5.5 `api/services/configuration/check_validity.py`

In `UserConfigurationValidator`'s provider-to-checker map, reuse the Google key check:

```python
            ServiceProviders.GOOGLE_REALTIME.value: self._check_google_api_key,
            ServiceProviders.GEMINI_LIVE_38.value: self._check_google_api_key,   # <- add
```

### 5.6 `api/services/pipecat/service_factory.py`

In `create_realtime_llm_service(user_config, audio_config)`, add this branch **after** the `GOOGLE_REALTIME` branch and before `GOOGLE_VERTEX_REALTIME`:

```python
    elif provider == ServiceProviders.GEMINI_LIVE_38.value:
        from api.services.pipecat.realtime.gemini_live import (
            COMPRESSION_TRIGGER_TOKENS,
        )
        from api.services.pipecat.realtime.gemini_live_38 import (
            DEFAULT_MODEL,
            DograhGemini38LiveLLMService,
        )
        from pipecat.services.google.gemini_live.llm import (
            ContextWindowCompressionParams,
        )

        settings_kwargs = {
            "model": model or DEFAULT_MODEL,
            "voice": voice or "Puck",
        }
        if COMPRESSION_TRIGGER_TOKENS > 0:
            settings_kwargs["context_window_compression"] = ContextWindowCompressionParams(
                enabled=True, trigger_tokens=COMPRESSION_TRIGGER_TOKENS
            )
        if language:
            settings_kwargs["language"] = language
        return DograhGemini38LiveLLMService(
            api_key=api_key,
            settings=DograhGemini38LiveLLMService.Settings(**settings_kwargs),
        )
```

`provider`, `model`, `voice`, `language` and `api_key` are the local variables the function already reads from `user_config.realtime`. Match whatever the existing `GOOGLE_REALTIME` branch in the target uses. **The safest approach is to copy that branch and change only the two class names.**

### 5.7 `api/services/pipecat/run_pipeline.py`

In `_create_realtime_user_turn_config(provider)`, add the provider to the Gemini set. Gemini's server-side VAD then owns barge-in while local Silero VAD stays on:

```python
    if provider in {
        ServiceProviders.GOOGLE_REALTIME.value,
        ServiceProviders.GEMINI_LIVE_38.value,     # <- add
        ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
    }:
        # Let Gemini Live own barge-in via its server-side VAD, but keep local
        ...
```

If you skip this, 3.8 calls get the generic realtime turn configuration, and interruptions behave differently from 3.1.

### 5.8 `api/services/integrations/tuner/runtime.py`

In `_resolve_model_labels`, so Tuner labels the model as a realtime model:

```python
        if realtime_provider in {
            ServiceProviders.GOOGLE_REALTIME.value,
            ServiceProviders.GEMINI_LIVE_38.value,     # <- add
            ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
            ServiceProviders.OPENAI_REALTIME.value,
        }:
```

Skip this if the target has no Tuner integration.

### 5.9 Node-transition "deafness" guard (required, shared by 3.1 and 3.8)

**The bug (run 600, upstream `fa1df112`):**

1. When a workflow moves to a new node, the Gemini service reconnects and then seeds the fresh session with the conversation so far.
2. If the node-transition context frame arrives **while the old session is still being torn down**, `self._session` still points at the dying session, so the `not self._session` check doesn't stop the seed.
3. The seed runs against the dying session, which clears the transition flags.
4. The fresh session is then never seeded, so **everything the caller says after the transition is ignored**. The call then often ends with a Gemini `1011` error.

**The fix:** in `api/services/pipecat/realtime/gemini_live.py`, in `_maybe_seed_node_transition_context`, add `or self._disconnecting` to the early-return condition:

```python
        if (
            not self._awaiting_node_transition_context
            or not self._node_transition_context_received
            or not self._session
            # A node-transition context frame can arrive while the reconnect's
            # disconnect is still in flight: _session still points to the old
            # session being torn down, so `not self._session` above does not yet
            # protect us. Seeding here would run against the dying session and
            # clear the node-transition flags, so the real seed never happens
            # when the fresh session is ready, and every word the caller says
            # after the transition is dropped. Wait until the reconnect settles.
            # (Upstream dograh fa1df112; seen here on run 600.)
            or self._disconnecting
            or self._node_transition_context_seed_started
        ):
            return
```

Nothing else changes: `_handle_session_ready` already re-runs the seed once the new session is ready. If the target's Dograh already contains upstream `fa1df112`, skip this step.

---

## 6. Recommended: silent opening after a recorded greeting (`c535bd4a`)

**The bug:**

1. When a workflow's start node uses a **recorded (audio) greeting**, the audio is sent straight to the phone line.
2. Gemini Live never hears about it, so it doesn't know the call has started or what was said.
3. Its first reply **greets the caller a second time**.

**The fix:**

- The engine hands the recording's transcript to the Gemini service.
- The service seeds it as a model turn with `turn_complete=False`. That means "here is what you already said, but don't reply", so Gemini waits for the caller.
- It applies to both 3.1 and 3.8. Ported from upstream `9c82ceb0` (Gemini part only).

Three files change.

### 6.1 `api/services/pipecat/audio_playback.py`

Add `LLMAssistantPushAggregationFrame` to the frames import:

```python
from pipecat.frames.frames import (
    Frame,
    LLMAssistantPushAggregationFrame,   # <- add
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    ...
)
```

In `play_audio(...)`, directly after the final `await queue_frame(TTSStoppedFrame(context_id=context_id))`, add:

```python
    await queue_frame(TTSStoppedFrame(context_id=context_id))
    if transcript and append_to_context:
        # Nothing else closes an assistant turn opened by recorded audio: only a
        # TTS service emits this frame, and the recording bypasses TTS. Without
        # it the transcript sits in the aggregator until the next LLM response
        # ends, and is committed as part of *that* message. (Upstream 9c82ceb0.)
        await queue_frame(LLMAssistantPushAggregationFrame())
```

### 6.2 `api/services/pipecat/realtime/gemini_live.py`

**(a)** In `__init__`, next to `self._pending_initial_greeting_text`:

```python
        # Greeting a recording already delivered, held until a session exists.
        # A 1-tuple, because the transcript itself may legitimately be None.
        self._pending_prerecorded_greeting: tuple[str | None] | None = None
```

**(b)** Add two methods. Put them just before `_handle_initial_greeting`:

```python
    async def handle_prerecorded_greeting(
        self, context: LLMContext | None, transcript: str | None
    ) -> None:
        """Open the conversation after a recording has greeted the caller.

        A recorded greeting is played straight to the output transport, so the
        opening TTSSpeakFrame never reaches this service: without this, Gemini
        is never told the call started or what it already said, and its first
        reply greets the caller a second time. Seed the greeting the caller
        heard as a model turn, accept caller audio, and generate nothing.

        ``transcript`` arrives here rather than through LLMContext because the
        recording is still playing; the aggregator commits it only once
        playback drains, after this seed is sent. (Ported from upstream dograh
        9c82ceb0, Gemini part.)
        """
        if self._handled_initial_context:
            return
        if context is None:
            logger.warning(
                f"{self}: received prerecorded greeting before context was set"
            )
            return
        self._handled_initial_context = True
        self._context = context
        # A fresh session never issued the tool calls in this history, so mark
        # their results delivered rather than sending them as tool responses.
        await self._process_completed_function_calls(send_new_results=False)
        self._pending_tool_results.clear()
        await self._create_prerecorded_greeting_response(transcript)

    async def _create_prerecorded_greeting_response(self, transcript: str | None):
        """Seed the spoken greeting, leaving the turn open for the caller.

        ``turn_complete=False`` is what separates this from every other
        opening: Gemini takes the history but is not asked to produce a turn,
        so it waits for the caller instead of greeting them again.
        """
        if self._disconnecting:
            return

        if not self._session:
            self._pending_prerecorded_greeting = (transcript,)
            self._run_llm_when_session_ready = True
            return

        self._pending_prerecorded_greeting = None

        adapter = self.get_llm_adapter()
        turns = list(
            adapter.get_llm_invocation_params(self._context).get("messages", [])
        )
        if transcript:
            turns.append(Content(role="model", parts=[Part(text=transcript)]))
        if not self._is_gemini_3 and (
            not turns or getattr(turns[-1], "role", None) != "user"
        ):
            # Gemini 2.5 requires a seed to end on a user turn; padding one that
            # already does would put two user turns back to back.
            turns.append(Content(role="user", parts=[Part(text=" ")]))

        logger.debug("Seeding Gemini Live with a prerecorded greeting")

        try:
            if turns:
                await self._session.send_client_content(
                    turns=turns, turn_complete=False
                )
        except Exception as e:
            await self._handle_send_error(e)

        if not self._is_gemini_3:
            # 2.5 only picks seeded history up once a turn completes; let the
            # caller's first utterance carry that completion.
            self._needs_initial_turn_complete_message = True

        self._ready_for_realtime_input = True
```

`Content` and `Part` come from `google.genai.types`, and `LLMContext` from `pipecat.processors.aggregators.llm_context`. Our `gemini_live.py` already imports them; add them if the target's doesn't.

**(c)** In `_handle_session_ready`, in the branch that handles "context arrived before session was ready", check the pending recorded greeting **before** the text greeting:

```python
            self._run_llm_when_session_ready = False
            if self._pending_prerecorded_greeting is not None:
                (transcript,) = self._pending_prerecorded_greeting
                await self._create_prerecorded_greeting_response(transcript)
            elif self._pending_initial_greeting_text is not None:
                await self._create_initial_greeting_response(
                    self._pending_initial_greeting_text
                )
            ...
```

### 6.3 `api/services/workflow/pipecat_engine.py`

In `queue_node_opening(...)`, in the audio-greeting branch, right after `play_audio(..., transcript=result.transcript, append_to_context=True)` and before `return "greeting"`:

```python
                            transcript=result.transcript,
                            append_to_context=True,
                        )
                        await self._open_realtime_after_recorded_greeting(
                            result.transcript
                        )
                        return "greeting"
```

Then add this method to `PipecatEngine`, for example just before `_handle_end_node`:

```python
    async def _open_realtime_after_recorded_greeting(
        self, transcript: str | None
    ) -> None:
        """Hand the opening turn to a realtime service after a recording.

        A recorded greeting is queued straight to the output transport, so a
        realtime service (Gemini Live) is otherwise never told the call has
        started or what was said, and greets the caller again on its first
        reply. The transcript goes with it because the recording is still
        playing: the assistant aggregator only commits it to LLMContext once
        playback drains. A no-op for text LLMs, which re-read the context on
        every generation. (Upstream dograh 9c82ceb0.)
        """
        handle_prerecorded_greeting = getattr(
            self.llm, "handle_prerecorded_greeting", None
        )
        if handle_prerecorded_greeting is None:
            return
        await handle_prerecorded_greeting(self.context, transcript)
```

Because of the `getattr` check, text LLMs and other realtime providers are unaffected.

---

## 7. Optional: switching which provider the default is

Nothing in this integration changes defaults. Existing agents stay on whatever they use, and nobody is moved to 3.8 automatically. To make 3.8 the default for *new* agents, change the target's default realtime configuration separately. We did **not** do this.

---

## 8. Tests

### 8.1 New file: `api/tests/test_gemini_live_38.py`

```python
"""Gemini 3.8 Live as its own provider, isolated from the 3.1 path.

On a Vectus call, 3.8 on the 3.1 code invented the Area Manager instead of
waiting for ``vectus_area_manager_lookup``: 3.8 runs tools NON_BLOCKING unless
a declaration says BLOCKING, and the pinned pipecat never declares BLOCKING.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.configuration.registry import (
    REALTIME_PROVIDERS,
    GeminiLive38LLMConfiguration,
    ServiceProviders,
)
from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService
from api.services.pipecat.realtime.gemini_live_38 import (
    DograhGemini38LiveLLMService,
    tag_tool_behaviors,
)


class _Test38(DograhGemini38LiveLLMService):
    def create_client(self):
        self._client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=None)))


class _Test31(DograhGeminiLiveLLMService):
    def create_client(self):
        self._client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=None)))


def _service(cls, model):
    service = cls(api_key="k", settings=cls.Settings(model=model))
    service.push_error = AsyncMock()
    return service


def _tools():
    return [
        {
            "function_declarations": [
                {"name": "vectus_area_manager_lookup", "parameters": {}},
                {"name": "move_to_main_agenda", "parameters": {}},
                {"name": "log_lead_in_background", "parameters": {}},
            ]
        }
    ]


def _session():
    return SimpleNamespace(send_tool_response=AsyncMock(), send_realtime_input=AsyncMock())


async def _noop(params):
    pass


# ─── tool behaviour ────────────────────────────────────────────────────────


def test_sync_tools_block_and_async_tools_do_not():
    tools = _tools()
    tagged = tag_tool_behaviors(tools, lambda name: name == "log_lead_in_background")
    behaviors = {d["name"]: d["behavior"] for d in tools[0]["function_declarations"]}
    assert behaviors == {
        "vectus_area_manager_lookup": "BLOCKING",
        "move_to_main_agenda": "BLOCKING",
        "log_lead_in_background": "NON_BLOCKING",
    }
    assert tagged == 3


def test_odd_tool_shapes_are_left_alone():
    assert tag_tool_behaviors(None, lambda n: False) == 0
    odd = [object(), {"x": 1}, {"function_declarations": "?"}]
    assert tag_tool_behaviors(odd, lambda n: False) == 0


@pytest.mark.asyncio
async def test_the_session_config_carries_the_behaviours():
    service = _service(_Test38, "gemini-3.8-live")
    service.register_function("log_lead_in_background", _noop, cancel_on_interruption=False)
    service.register_function("vectus_area_manager_lookup", _noop)
    config = SimpleNamespace(tools=_tools())
    with patch.object(
        DograhGeminiLiveLLMService, "_connection_task_handler", AsyncMock()
    ) as parent:
        await service._connection_task_handler(config)
    parent.assert_awaited_once()
    decls = {d["name"]: d.get("behavior") for d in config.tools[0]["function_declarations"]}
    assert decls["vectus_area_manager_lookup"] == "BLOCKING"
    assert decls["log_lead_in_background"] == "NON_BLOCKING"


# ─── tool results ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tool_result_is_sent_without_the_old_3x_nudge():
    service = _service(_Test38, "gemini-3.8-live")
    session = _session()
    service._session = session
    service._disconnecting = False

    assert await service._tool_result("id-1", "vectus_area_manager_lookup", {"status": "found"})

    sent = session.send_tool_response.await_args.kwargs["function_responses"]
    assert (sent.name, sent.id, sent.response) == (
        "vectus_area_manager_lookup",
        "id-1",
        {"status": "found"},
    )
    session.send_realtime_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_async_tool_result_asks_to_wait_until_idle():
    service = _service(_Test38, "gemini-3.8-live")
    service.register_function("log_lead_in_background", _noop, cancel_on_interruption=False)
    session = _session()
    service._session = session
    service._disconnecting = False
    await service._tool_result("id-2", "log_lead_in_background", {"ok": True})
    sent = session.send_tool_response.await_args.kwargs["function_responses"]
    assert sent.response == {"ok": True, "scheduling": "WHEN_IDLE"}


@pytest.mark.asyncio
async def test_a_result_during_reconnect_is_queued():
    service = _service(_Test38, "gemini-3.8-live")
    service._session = None
    assert await service._tool_result("id-3", "vectus_area_manager_lookup", {"a": 1}) is False
    assert service._pending_tool_results["id-3"] == ("vectus_area_manager_lookup", {"a": 1})


# ─── the 3.1 path is untouched ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gemini_31_keeps_its_behaviour():
    service = _service(_Test31, "gemini-3.1-flash-live-preview")
    assert service._supports_non_blocking_tools is False
    config = SimpleNamespace(tools=_tools())
    with patch(
        "pipecat.services.google.gemini_live.llm.GeminiLiveLLMService._connection_task_handler",
        AsyncMock(),
    ):
        await service._connection_task_handler(config)
    assert all("behavior" not in d for d in config.tools[0]["function_declarations"])

    session = _session()
    service._session = session
    service._disconnecting = False
    await service._tool_result("id-4", "vectus_area_manager_lookup", {"status": "found"})
    session.send_realtime_input.assert_awaited_once_with(text=" ")  # 3.1 still nudges


# ─── provider wiring ───────────────────────────────────────────────────────


def test_gemini_38_is_its_own_realtime_provider():
    config = GeminiLive38LLMConfiguration(api_key="k")
    assert config.provider == ServiceProviders.GEMINI_LIVE_38
    assert config.model == "gemini-3.8-live"
    assert ServiceProviders.GEMINI_LIVE_38.value in REALTIME_PROVIDERS


def test_the_factory_builds_the_38_service_only_for_its_provider():
    from api.services.pipecat.service_factory import create_realtime_llm_service

    def build(provider, model):
        realtime = SimpleNamespace(
            provider=provider, model=model, api_key="k", voice="Puck", language="hi"
        )
        return create_realtime_llm_service(SimpleNamespace(realtime=realtime), None)

    assert type(build("gemini_live_38", "gemini-3.8-live")) is DograhGemini38LiveLLMService
    assert (
        type(build("google_realtime", "gemini-3.1-flash-live-preview"))
        is DograhGeminiLiveLLMService
    )
```

### 8.2 Guard regression test

Append this to `api/tests/test_gemini_live_reconnect_tool_results.py`. It reuses that file's existing helpers `_make_service`, `_FakeSession` and `_make_tool_result_context`:

```python
@pytest.mark.asyncio
async def test_node_transition_context_frame_during_disconnect_defers_seed():
    """A node-transition context frame that arrives while the reconnect's
    disconnect is still in flight must not seed against the dying session.

    Regression (upstream dograh fa1df112; run 600 here): `self._session` still
    points to the old session mid-disconnect, so the `not self._session` guard
    did not protect the seed. Seeding there ran against a session being torn
    down and cleared the node-transition flags, so the fresh session was never
    seeded and the bot stopped hearing the caller after the transition.
    """
    service = _make_service()
    service._handled_initial_context = True
    service._awaiting_node_transition_context = True
    service._node_transition_context_received = False
    service._process_completed_function_calls = AsyncMock()
    service._create_initial_response = AsyncMock()

    # Mid-reconnect: the old session is still present and the disconnect is in
    # progress. The transition's context frame lands in exactly this window.
    service._session = _FakeSession()
    service._disconnecting = True

    await service._handle_context(_make_tool_result_context("call-transition"))

    service._create_initial_response.assert_not_awaited()
    assert service._awaiting_node_transition_context is True
    assert service._node_transition_context_received is True

    # Reconnect settles: the fresh session becomes ready and seeds exactly once.
    service._disconnecting = False
    await service._handle_session_ready(_FakeSession())

    service._create_initial_response.assert_awaited_once()
    assert service._awaiting_node_transition_context is False
```

### 8.3 Recorded-greeting tests (only if you applied section 6)

New file `api/tests/test_recorded_greeting_realtime.py`. It imports `FAKE_PCM_AUDIO` and the `audio_workflow` fixture from the existing `api/tests/test_text_and_audio_playback.py`; check that this file exists in the target.

```python
"""A recorded (audio) greeting on a realtime model (ported from upstream 9c82ceb0).

The recording is played straight to the transport, so Gemini Live never sees
the opening turn. Without a handoff its session is unseeded: it does not know
the call has started or what was said, and its first reply greets the caller
again. The engine now hands the greeting to the service, which seeds it as a
model turn without asking for a reply.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.frames.frames import LLMAssistantPushAggregationFrame, TTSTextFrame
from pipecat.processors.aggregators.llm_context import LLMContext

from api.services.pipecat.audio_playback import play_audio
from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService
from api.services.pipecat.realtime.gemini_live_38 import DograhGemini38LiveLLMService
from api.services.pipecat.recording_audio_cache import RecordingAudio
from api.services.workflow.pipecat_engine import PipecatEngine
from api.tests.test_text_and_audio_playback import (  # noqa: F401 (fixture)
    FAKE_PCM_AUDIO,
    audio_workflow,
)


def _client_stub(self):
    self._client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=None)))


class _Gemini31(DograhGeminiLiveLLMService):
    create_client = _client_stub


class _Gemini38(DograhGemini38LiveLLMService):
    create_client = _client_stub


def _session():
    return SimpleNamespace(
        send_client_content=AsyncMock(),
        send_realtime_input=AsyncMock(),
        send_tool_response=AsyncMock(),
    )


def _engine(workflow, llm, context):
    engine = PipecatEngine(
        llm=llm,
        context=context,
        workflow=workflow,
        call_context_vars={},
        workflow_run_id=1,
    )
    # Recordings are stored by numeric id (the shared fixture uses a label).
    engine.get_node_greeting = lambda node_id: ("audio", "5")
    engine.set_transport_output(Mock(queue_frame=AsyncMock()))
    engine.set_fetch_recording_audio(
        AsyncMock(return_value=RecordingAudio(FAKE_PCM_AUDIO, "Namaskar, main Aastha"))
    )
    return engine


# ─── the engine hands the greeting over ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_recorded_greeting_is_handed_to_the_realtime_service(audio_workflow):
    llm = Mock(spec=["handle_prerecorded_greeting"])
    llm.handle_prerecorded_greeting = AsyncMock()
    context = LLMContext()
    engine = _engine(audio_workflow, llm, context)

    result = await engine.queue_node_opening(
        node_id=audio_workflow.start_node_id,
        previous_node_id=None,
        generate_if_no_greeting=True,
    )

    assert result == "greeting"
    llm.handle_prerecorded_greeting.assert_awaited_once_with(
        context, "Namaskar, main Aastha"
    )
    queued = [c.args[0] for c in engine._transport_output.queue_frame.await_args_list]
    assert [type(f).__name__ for f in queued] == [
        "TTSStartedFrame",
        "TTSTextFrame",
        "TTSAudioRawFrame",
        "TTSStoppedFrame",
        "LLMAssistantPushAggregationFrame",  # the greeting is its own turn
    ]


@pytest.mark.asyncio
async def test_a_text_llm_without_a_session_is_left_alone(audio_workflow):
    llm = Mock(spec=["queue_frame"])  # no handle_prerecorded_greeting
    engine = _engine(audio_workflow, llm, LLMContext())
    result = await engine.queue_node_opening(
        node_id=audio_workflow.start_node_id,
        previous_node_id=None,
        generate_if_no_greeting=True,
    )
    assert result == "greeting"


@pytest.mark.asyncio
async def test_a_recording_kept_out_of_context_does_not_close_a_turn():
    """Transition recordings (append_to_context=False) are unchanged."""
    queue = AsyncMock()
    await play_audio(
        FAKE_PCM_AUDIO, sample_rate=8000, queue_frame=queue,
        transcript="Aap nishchint rahein", persist_to_logs=True,
    )
    frames = [c.args[0] for c in queue.await_args_list]
    assert not any(isinstance(f, LLMAssistantPushAggregationFrame) for f in frames)
    assert any(isinstance(f, TTSTextFrame) for f in frames)


# ─── Gemini is told, but not asked to speak ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls,model",
    [(_Gemini31, "gemini-3.1-flash-live-preview"), (_Gemini38, "gemini-3.8-live")],
)
async def test_gemini_seeds_the_greeting_without_asking_for_a_reply(cls, model):
    service = cls(api_key="k", settings=cls.Settings(model=model))
    session = _session()
    service._session = session
    service._disconnecting = False
    service._ready_for_realtime_input = False
    context = LLMContext()

    await service.handle_prerecorded_greeting(context, "Namaskar, main Aastha")

    kwargs = session.send_client_content.await_args.kwargs
    assert kwargs["turn_complete"] is False  # not asked to produce a turn
    last = kwargs["turns"][-1]
    assert last.role == "model" and last.parts[0].text == "Namaskar, main Aastha"
    session.send_realtime_input.assert_not_awaited()  # no generation nudge
    assert service._ready_for_realtime_input is True  # caller audio accepted
    assert service._handled_initial_context is True

    # A later greeting trigger must not open the conversation a second time.
    await service.handle_prerecorded_greeting(context, "again")
    assert session.send_client_content.await_count == 1


@pytest.mark.asyncio
async def test_before_the_session_connects_the_greeting_waits_for_it():
    service = _Gemini38(api_key="k", settings=_Gemini38.Settings(model="gemini-3.8-live"))
    service._session = None
    service._disconnecting = False

    await service.handle_prerecorded_greeting(LLMContext(), "Namaskar")
    assert service._pending_prerecorded_greeting == ("Namaskar",)
    assert service._run_llm_when_session_ready is True

    session = _session()
    await service._handle_session_ready(session)

    kwargs = session.send_client_content.await_args.kwargs
    assert kwargs["turn_complete"] is False
    assert kwargs["turns"][-1].parts[0].text == "Namaskar"
    assert service._pending_prerecorded_greeting is None
    session.send_realtime_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_context_yet_is_ignored_safely():
    service = _Gemini31(api_key="k", settings=_Gemini31.Settings(model="gemini-3.1-flash-live-preview"))
    await service.handle_prerecorded_greeting(None, "Namaskar")
    assert service._handled_initial_context is False
```

### 8.4 Running the tests

Tests need the test Postgres and Redis running (see `docs/contribution/setup.mdx`):

```bash
docker compose -f docker-compose-local.yaml up -d postgres redis

source venv/bin/activate
set -a && source api/.env.test && set +a      # never api/.env: tests must hit the test DB
PYTHONUTF8=1 python -m pytest \
  api/tests/test_gemini_live_38.py \
  api/tests/test_gemini_live_reconnect_tool_results.py \
  api/tests/test_recorded_greeting_realtime.py -q
```

Then run the wider Gemini, pipeline and configuration tests, and finally the full suite:

```bash
PYTHONUTF8=1 python -m pytest api/tests -q -k "gemini or realtime or registry or service_factory or configuration"
PYTHONUTF8=1 python -m pytest api/tests -q
```

**Windows notes:**

- `PYTHONUTF8=1` avoids `charmap` encoding errors.
- If more than one pipecat is installed on the machine, make sure the tests import **this repo's** submodule. Prefix the command with `PYTHONPATH=pipecat/src`, or install the submodule into the venv with `pip install -e ./pipecat`.

**Expected:** all three files pass (9 + the guard test + 7). Our run passed them all, with no regressions in the rest of the suite.

---

## 9. Deploying (Docker Compose on a VM)

**Only the API image changes**, so the UI doesn't need a rebuild for this feature.

Run each command **separately**, each starting with an absolute `cd`. A pasted block once kept running after a failed `cd` and restarted production by accident.

```bash
# 1. Make sure no call is in progress (should print 0). Restarting api drops live calls.
cd ~/dograh && docker compose exec -T postgres psql -U postgres -d postgres -tAc "select count(*) from workflow_runs where not is_completed and created_at > now() - interval '30 minutes';"
```
```bash
# 2. Get the code
cd ~/dograh && git pull --ff-only origin <your-branch>
```
```bash
# 3. Build the api image (this is where constraints.txt matters)
cd ~/dograh && docker compose build api
```
```bash
# 4. Restart only api (don't touch postgres, redis or minio)
cd ~/dograh && docker compose up -d --no-deps api
```
```bash
# 5. Check that it's healthy
cd ~/dograh && docker compose ps
cd ~/dograh && docker compose logs --tail=100 api
```

Adjust the database name (`-d postgres`) to wherever the target's schema lives.

**Never** run `docker compose down -v` or `docker system prune --volumes`. They delete the database volume.

If you also applied section 6, it's still API-only. No migrations are needed.

**Check the installed packages after the build:**

```bash
cd ~/dograh && docker compose exec -T api pip show google-genai | grep -i version   # expect 2.24.0
```

---

## 10. Configuring an agent to use Gemini 3.8

1. In the Dograh UI, open **Model settings** (organization or agent level, wherever the target sets the realtime model).
2. Turn on realtime mode, if the target has a separate toggle, and choose provider **"Gemini 3.8 Live"**.
3. Set:
   - **Model:** `gemini-3.8-live` (the default);
   - **API key:** the same **Google AI Studio** key used for "Google Realtime". Vertex keys don't work; this is AI Studio only;
   - **Voice:** for example `Puck`, `Charon`, `Kore`, `Fenrir` or `Aoede`;
   - **Language:** for example `hi` or `en`.
4. Save, and **publish** the agent. Calls run the **published** configuration, so a saved draft isn't enough.
5. Place a test call that triggers a tool.

---

## 11. Checking that it really runs on 3.8

**1. API logs at call start** should show the provider and model:

```bash
cd ~/dograh && docker compose logs --since=10m api | grep -i "gemini_live_38\|gemini-3.8-live"
```

Expect something like `provider=ServiceProviders.GEMINI_LIVE_38, model=gemini-3.8-live`.

**2. Tool behaviour is declared.** This needs `LOG_LEVEL=DEBUG` in `.env`, then `docker compose up -d --no-deps api`:

```bash
cd ~/dograh && docker compose logs --since=10m api | grep "declared behavior on"
```

Expect `DograhGemini38LiveLLMService#0: declared behavior on N Gemini 3.8 tool(s)`. Set `LOG_LEVEL` back to `INFO` afterwards, because DEBUG is very verbose.

**3. The tool actually ran:**

```bash
cd ~/dograh && docker compose logs --since=10m api | grep "Executing custom tool"
```

**4. The run record**, from the database:

```sql
select id,
       initial_context->'runtime_configuration'->>'realtime_provider' as provider,
       initial_context->'runtime_configuration'->>'realtime_model'    as model
from workflow_runs order by id desc limit 5;
```

Expect `gemini_live_38` and `gemini-3.8-live`. The exact JSON keys may differ slightly by version; inspect `initial_context` if these are null.

**5. Behaviour on the call:** after the caller gives the details a tool needs, the agent should pause, the tool should run, and the agent should then speak the **real** result. It shouldn't produce a plausible-sounding invented answer.

### Our production results (for reference)

- **Run 602 (3.8):** the real `vectus_area_manager_lookup` result, the correct Area Manager, and both WhatsApp messages delivered.
- **Latency over 3 days, measured from the run logs:**

  | | Calls | Median response | p90 | First reply |
  |---|---|---|---|---|
  | Gemini 3.1 | 56 | 1.93 s | 3.77 s | 2.51 s |
  | Gemini 3.8 | 5 | 1.75 s | 4.16 s | 2.93 s |

  The pause on a turn that calls a tool is about **7 s on both** models. That's the tool's HTTP call plus the model's turn, not a 3.8 problem. The 3.8 sample is small.

---

## 12. Known limitations and gotchas

1. **Text custom messages don't play on Gemini Live.** An HTTP tool's "custom message" of type **text** is sent as a `TTSSpeakFrame`, which Gemini Live (3.1 and 3.8) ignores. Use an **audio (recorded)** custom message instead.
2. **Extended-thinking 3.8 isn't supported** until pipecat is upgraded to ≥ v1.12 (section 1).
3. **Silence after a handoff on 3.8** has been reported upstream (Gemini cookbook issue #1390). If an agent goes quiet right after a node transition, check that section 5.9 is applied, then compare with 3.1.
4. **`1011` "internal error" disconnects.** Google suggests disabling its server-side compression when these happen. To test that, set `GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS=0`, keeping the variable listed in compose's `environment:` (section 3.2). Our compaction (`GEMINI_LIVE_CONTEXT_COMPACTION_TRIGGER_TOKENS=5000`) keeps reconnect seeds small.
5. **When a tool gets called is decided by the prompt.** BLOCKING makes the model *wait* for a tool it calls, but it doesn't make the model *call* it. The node prompt must still say when to call the tool, and that it must not answer without the result.
6. **Dependency drift.** Without `constraints.txt`, a rebuild can upgrade `google-genai` and silently break Gemini Live speech recognition (section 3.1). If the bot greets and then never responds, check `pip show google-genai` first.
7. **Draft vs published.** Configuration changes take effect only after publishing the agent.

---

## 13. Rollback

- **Per agent, instantly:** switch the provider back to **"Google Realtime"** (model `gemini-3.1-flash-live-preview`) and publish. No deploy is needed.
- **Removing the code:** revert the commit or commits and redeploy the API.
  - First move every agent off `gemini_live_38`. Otherwise their saved configurations no longer validate, because the enum value is gone.
  - Find them with:
    ```sql
    select id, name from workflows where workflow_configurations::text like '%gemini_live_38%';
    ```
    Adjust the table and column names to the target, and also check the organization- or user-level configuration tables.
- The section 5.9 guard and section 6 greeting fix are safe to keep even if you remove the 3.8 provider.

---

## 14. The long-term path

This backport exists because upgrading pipecat app-wide is a big merge. When the target moves to **pipecat ≥ v1.12** (upstream Dograh is on `41a5b9e`, v1.12.0):

1. Pipecat itself tags tool behaviours and drops the nudge, so `DograhGemini38LiveLLMService` becomes redundant.
2. Keep the `gemini_live_38` provider value (saved configurations reference it), but point its factory branch at the plain `DograhGeminiLiveLLMService`. Alternatively, migrate those configurations to `google_realtime` with model `gemini-3.8-live`.
3. Delete `gemini_live_38.py`, and update `test_gemini_live_38.py` to match.
4. Extended-thinking 3.8 then becomes possible.

---

## 15. Reference: commits and files

| Commit | Title |
|---|---|
| `47a96244` | feat(gemini-live): add Gemini 3.8 Live as its own provider, and stop the post-transition deafness |
| `c535bd4a` | fix(realtime): open Gemini Live silently after a recorded greeting |
| `02d1075a` | dependency pins (google-genai 2.24.0, constraints.txt, uv 0.12.21) |
| upstream `fa1df112` | origin of the node-transition guard |
| upstream `9c82ceb0` | origin of the recorded-greeting fix |

| Commit | Title |
|---|---|
| `b4315db7` | feat(gemini-live): refresh the session to shed accumulated audio history |
| `a66dd0ba` | fix(gemini_live): stop superseded connections from triggering reconnects |
| `d2f5b40d` | fix(build): pin every Python package to the pre-2026-09-30 set |
| `83ebf437` | chore(compose): let .env set the api's LOG_LEVEL |
| `4721ae7c` | chore(compose): pass the Gemini Live cost controls through to the api |

### Applying everything with the patch files (tested)

`handoff/patches/` holds the eight commits as patch files, numbered in the order to apply them:

```
0001-fix-gemini-live-pin-google-genai-2.24.0-and-the-uv-i.patch
0002-fix-build-pin-every-Python-package-to-the-pre-2026-0.patch
0003-chore-compose-let-.env-set-the-api-s-LOG_LEVEL.patch
0004-feat-gemini-live-refresh-the-session-to-shed-accumul.patch
0005-chore-compose-pass-the-Gemini-Live-cost-controls-thr.patch
0006-fix-gemini_live-stop-superseded-connections-from-tri.patch
0007-feat-gemini-live-add-Gemini-3.8-Live-as-its-own-prov.patch
0008-fix-realtime-open-Gemini-Live-silently-after-a-recor.patch
```

Copy the folder into the target repo, then run:

```bash
git checkout -b gemini-3.8-live
git am -3 handoff/patches/*.patch
```

**Tested on 2026-10-02 against a clean upstream Dograh** (commit `2f7b47a1`, pipecat `aadd1d5`). All eight applied, with **two small conflicts**. Both happen because upstream lacks unrelated additions our fork had made nearby:

1. **Patch 0001, `api/requirements.txt`.** The patch's context includes our extra lines (`tzdata`, `openai`, `vakyamai`). Resolve by keeping the target's lines and adding only:
   ```
   google-genai==2.24.0
   ```
   Then run `git add api/requirements.txt && git am --continue`.
2. **Patch 0005, `docker-compose.yaml`.** The patch's context includes our AI-assistant and Code Editor env vars. Resolve by deleting those and keeping **only** the `# Gemini Live cost controls` comment block and its two `GEMINI_LIVE_*` lines (Appendix C), inside the api service's `environment:`. Then run `git add docker-compose.yaml && git am --continue`.

After that, these tests all pass in the patched copy (**54 passed**): the six Gemini test files, `test_gemini_live_38`, `test_gemini_live_reconnect_tool_results`, `test_recorded_greeting_realtime`, `test_gemini_live_context_compaction`, `test_gemini_live_reconnect_race` and `test_gemini_live_audio_wire_format`.

A target that has drifted further from upstream may hit more conflicts. Use sections 5 and 6 and Appendices A–H as the reference when resolving them. Recheck `api/constraints.txt` against the target's own packages (Appendix E).

**Alternative, if the target can reach our GitHub repo:**

```bash
git remote add botrix https://github.com/Jatin-35/dograh.git
git fetch botrix botrix-main
git cherry-pick 02d1075a d2f5b40d 83ebf437 b4315db7 4721ae7c a66dd0ba 47a96244 c535bd4a
```

---

# Appendices: every file and diff, verbatim

Everything below is generated from the repository at `botrix-main` (HEAD `da222c79`), not retyped. Full files are the final state. Diffs are exact `git show` output, and can be applied with `git apply` if the target's surrounding code matches.

## Appendix A: complete final `api/services/pipecat/realtime/gemini_live.py`

This is the whole file, all 755 lines. It contains:

- the session refresh / compaction (`b4315db7`);
- the reconnect race fix (`a66dd0ba`);
- the node-transition guard (`47a96244`, from upstream `fa1df112`);
- the recorded-greeting handling (`c535bd4a`).

It works against pipecat `aadd1d5dd606d2871b082e6f2ca1ad1eee53785b`, the submodule pin of both upstream Dograh at `2f7b47a1` and our fork. If the target pins that same pipecat commit, you can replace its file with this one outright.

```python
"""Dograh subclass of pipecat's Gemini Live LLM service.

Layers Dograh engine integration quirks onto upstream-pristine
:class:`GeminiLiveLLMService`:

- **Deferred connect.** Connection is held back until ``system_instruction``
  is set via :meth:`_update_settings`, so pre-call-fetch template variables
  land before the live session opens.
- **Reconnect on node transitions.** Gemini Live cannot update
  ``system_instruction`` mid-session, so a setting change triggers a
  reconnect (deferred until the bot turn ends if currently responding).
- **Node-transition deferral.** Node-transition calls emitted mid-turn are
  queued and run when the bot stops speaking, to avoid cutting off its audio.
- **User-mute audio gating.** ``UserMuteStarted/StoppedFrame`` from the
  user aggregator gates whether incoming audio is forwarded to Gemini.
- **TTSSpeakFrame as greeting trigger.** The engine queues a TTSSpeakFrame
  to kick off the first response after node setup; the service intercepts
  it and runs the initial-context path.
"""

import asyncio
import contextvars
import os
from typing import Any

from google.genai.types import Content, LiveServerMessage, Part
from loguru import logger

from api.services.pipecat.gemini_json_schema_adapter import (
    DograhGeminiJSONSchemaAdapter,
)
from api.services.pipecat.realtime.static_greeting import format_static_greeting_prompt
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    TTSSpeakFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
from pipecat.services.llm_service import FunctionCallFromLLM
from pipecat.utils.tracing.service_decorators import traced_gemini_live

# Gemini Live bills the whole accumulated context on every model turn, and holds
# spoken history in the session as audio — far more tokens than the equivalent
# transcript, at four times the per-token price. A reconnect reseeds the fresh
# session from LLMContext, which holds text, so refreshing the session once the
# context passes this many tokens trades one reconnect for a much cheaper
# context from there on.
#
# Off by default: the refresh costs a reconnect (~1s on this pipeline), during
# which the user hears no difference but the bot is not listening. Measure that
# gap on a test call before turning it on, and pick a trigger high enough that it
# fires once on a long call rather than every few turns.
CONTEXT_COMPACTION_TRIGGER_TOKENS = int(
    os.getenv("GEMINI_LIVE_CONTEXT_COMPACTION_TRIGGER_TOKENS", "0")
)

# Cap on user audio held during a compaction refresh. Sized well past the
# expected reconnect so a stalled one sheds the oldest audio instead of growing
# without bound. ~5s of 16 kHz mono PCM.
MAX_COMPACTION_BUFFERED_AUDIO_BYTES = 16_000 * 2 * 5

# Server-side sliding-window compression. Bounds growth between compactions and
# lifts Gemini's 15-minute cap on uncompressed audio sessions. 0 disables.
COMPRESSION_TRIGGER_TOKENS = int(
    os.getenv("GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS", "16000")
)

# The connection epoch a connection's background loop was started under. Each
# connection runs in its own asyncio task, and a task gets its own copy of a
# context variable, so every loop can tell whether it has been superseded
# without changing upstream's loop. See _handle_connection_error.
_LOOP_CONNECTION_EPOCH: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "gemini_live_loop_connection_epoch", default=None
)


class DograhGeminiLiveLLMService(GeminiLiveLLMService):
    """Gemini Live with Dograh engine integration quirks. See module docstring."""

    # Gemini input transcription is delivered independently from tool calls.
    # Give late transcription messages a small window to arrive before running
    # a node-transition function and tearing down the current Live connection.
    _NODE_TRANSITION_TRANSCRIPTION_GRACE_SECONDS = 0.5

    # Route tool schemas through Gemini's ``parameters_json_schema`` field so
    # MCP/imported tools that use JSON Schema keywords (``const``, ``not``,
    # nested ``anyOf``) rejected by the strict ``Schema`` model are accepted.
    # Mirrors the non-realtime ``DograhGoogleLLMService`` fix;
    # ``DograhGeminiLiveVertexLLMService`` inherits this via MRO.
    adapter_class = DograhGeminiJSONSchemaAdapter

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # User-mute state, driven by broadcast UserMute{Started,Stopped}Frames.
        # Audio is not forwarded to Gemini while muted.
        self._user_is_muted: bool = False
        # Guards initial-response triggering against double-firing across the
        # initial TTSSpeakFrame and any LLMContextFrame that may arrive.
        self._handled_initial_context: bool = False
        # Node-transition calls emitted mid-bot-turn are deferred here so the
        # transition does not tear down Gemini while it is still producing audio.
        self._pending_node_transition_function_calls: list[FunctionCallFromLLM] = []
        # Text greeting captured from the first TTSSpeakFrame while the Gemini
        # session is still connecting.
        self._pending_initial_greeting_text: str | None = None
        # Greeting a recording already delivered, held until a session exists.
        # A 1-tuple, because the transcript itself may legitimately be None.
        self._pending_prerecorded_greeting: tuple[str | None] | None = None
        self._transition_function_call_task: asyncio.Task | None = None
        # Intentional node changes use a fresh, context-seeded connection rather
        # than a potentially stale session-resumption handle. The new connection
        # remains gated until the function-call result has landed in LLMContext.
        self._awaiting_node_transition_context: bool = False
        self._node_transition_context_received: bool = False
        self._node_transition_context_seed_started: bool = False
        # Cost compaction: set when the reported prompt size crosses the trigger,
        # acted on at the next safe point (bot turn ended, user not speaking).
        # Stays set until a compaction actually runs, so a skipped turn is
        # retried rather than dropped.
        self._context_compaction_pending: bool = False
        self._awaiting_context_compaction_seed: bool = False
        # User audio captured while the compaction reconnect is in flight.
        # Upstream discards audio whenever the session is down, so we hold it
        # here and replay it once the fresh session is seeded.
        self._compaction_audio_frames: list[Any] = []
        self._compaction_audio_bytes: int = 0
        # Advanced by every _disconnect(); a connection loop started under an
        # older value has been superseded. See _handle_connection_error.
        self._connection_epoch: int = 0

    # ------------------------------------------------------------------
    # Hooks from upstream GeminiLiveLLMService
    # ------------------------------------------------------------------

    def _should_connect_on_start(self) -> bool:
        # Hold the connection until the engine sets a system_instruction. This
        # lets pre-call fetch populate template variables first.
        return bool(self._settings.system_instruction)

    def _requires_node_transition_context_aggregation(self) -> bool:
        # A node transition replaces the current Gemini Live connection and
        # seeds the new one from our local LLMContext. Wait for the upstream
        # user aggregator to commit any final TranscriptionFrame before
        # set_node() changes the prompt and starts that reconnect.
        return True

    async def cleanup(self) -> None:
        """Cancel a delayed transition before tearing down the Live session."""
        if self._transition_function_call_task:
            await self.cancel_task(self._transition_function_call_task)
            self._transition_function_call_task = None
        self._discard_compaction_audio()
        await super().cleanup()

    async def _handle_changed_settings(self, changed: dict[str, Any]) -> set[str]:
        if "system_instruction" not in changed:
            return set()

        # PipecatEngine updates system_instruction only from set_node(). The
        # first set_node happens before a Live session exists; every later one
        # is a node transition whose tool call has already been deferred until
        # the current bot turn finishes.
        if not self._session:
            # First-time setting after deferred-connect.
            await self._connect()
        else:
            await self._reconnect_for_node_transition()
        return {"system_instruction"}

    async def _run_or_defer_function_calls(
        self, function_calls_llm: list[FunctionCallFromLLM]
    ):
        if not self._contains_node_transition(function_calls_llm):
            await super()._run_or_defer_function_calls(function_calls_llm)
            return

        # Keep a provider tool-call batch together. Splitting a mixed batch here
        # would discard Pipecat's shared function-call group and could trigger an
        # LLM run before every result from the original batch has arrived.
        if self._bot_is_responding:
            # Latest batch wins; Gemini emits tool calls as one batch per
            # tool_call message, so this overwrite is intentional.
            self._pending_node_transition_function_calls = function_calls_llm
            logger.debug(
                f"{self}: deferring {len(function_calls_llm)} node-transition "
                "function call(s) "
                "until bot turn ends"
            )
            return

        self._schedule_node_transition_function_calls(function_calls_llm)

    def _contains_node_transition(
        self, function_calls_llm: list[FunctionCallFromLLM]
    ) -> bool:
        return any(self._is_node_transition(fc) for fc in function_calls_llm)

    def _is_node_transition(self, function_call: FunctionCallFromLLM) -> bool:
        return self._function_is_node_transition(function_call.function_name)

    def _schedule_node_transition_function_calls(
        self, function_calls_llm: list[FunctionCallFromLLM]
    ) -> None:
        """Run transition calls after late input transcription has settled."""
        if (
            self._transition_function_call_task
            and not self._transition_function_call_task.done()
        ):
            logger.warning(
                f"{self}: node-transition function call already pending; "
                "ignoring duplicate batch"
            )
            return

        async def _run_after_transcription_grace() -> None:
            try:
                await asyncio.sleep(self._NODE_TRANSITION_TRANSCRIPTION_GRACE_SECONDS)
                await self._flush_pending_user_transcription()
                await self.run_function_calls(function_calls_llm)
            finally:
                self._transition_function_call_task = None

        self._transition_function_call_task = self.create_task(
            _run_after_transcription_grace(),
            name=f"{self}::node-transition-function-calls",
        )

    async def _flush_pending_user_transcription(self) -> None:
        """Publish any punctuationless user transcript before a node handoff."""
        if self._transcription_timeout_task:
            if not self._transcription_timeout_task.done():
                await self.cancel_task(self._transcription_timeout_task)
            self._transcription_timeout_task = None

        if not self._user_transcription_buffer:
            return

        text = self._user_transcription_buffer
        self._user_transcription_buffer = ""
        logger.debug(
            f"{self}: flushing pending user transcription before node transition"
        )
        await self._push_user_transcription(text, result=None)

    # ------------------------------------------------------------------
    # State-transition side effects
    # ------------------------------------------------------------------

    async def _set_bot_is_responding(self, responding: bool):
        was_responding = self._bot_is_responding
        await super()._set_bot_is_responding(responding)
        if was_responding and not responding:
            await self._run_pending_node_transition_function_calls()
            await self._maybe_compact_context()

    async def _run_pending_node_transition_function_calls(self):
        """Run any node-transition calls deferred during the bot's last turn."""
        if not self._pending_node_transition_function_calls:
            return
        fcs = self._pending_node_transition_function_calls
        self._pending_node_transition_function_calls = []
        logger.debug(
            f"{self}: executing {len(fcs)} deferred node-transition call(s) "
            "after bot turn ended"
        )
        self._schedule_node_transition_function_calls(fcs)

    async def _reconnect_for_node_transition(self) -> None:
        """Start a fresh connection and wait to seed the completed context.

        Gemini can report ``resumable=False`` while generating or executing a
        function call. A workflow transition happens at exactly that boundary,
        so using the last (older) resumption handle can omit the triggering user
        turn. Use the local LLMContext as the source of truth for this intentional
        handoff instead.
        """
        self._awaiting_node_transition_context = True
        self._node_transition_context_received = False
        self._node_transition_context_seed_started = False
        self._session_resumption_handle = None
        outgoing_task = self._connection_task
        await self._disconnect()
        await self._await_outgoing_connection_stopped(outgoing_task)
        await self._connect(session_resumption_handle=None)

    # ------------------------------------------------------------------
    # Superseded connections must not trigger reconnects (run-442)
    # ------------------------------------------------------------------
    #
    # Upstream's _disconnect() cancels the outgoing connection's loop with a
    # best-effort ~1s timeout and moves on whether or not it stopped. A loop
    # that survives then fails on its next read — self._session is already
    # cleared or belongs to the newer connection ("'NoneType' object has no
    # attribute 'receive'") — and upstream treats that as a real failure: it
    # counts it, reconnects on top of the newer connection, and after three
    # rounds ends the call with "Gemini Live connection failed after 3
    # consecutive attempts". These three overrides stop that without touching
    # upstream code: every error upstream reacts to goes through
    # _handle_connection_error, which now ignores errors from a loop that a
    # newer connection has replaced.

    async def _disconnect(self, *args, **kwargs):
        # First, before any await inside upstream's teardown can hand control
        # back to the outgoing loop. Arguments pass through: newer pipecat
        # takes _disconnect(preserve_pending_end_frame=...).
        self._connection_epoch += 1
        return await super()._disconnect(*args, **kwargs)

    async def _connection_task_handler(self, config):
        # Runs inside the connection's own task, so this binding is private to
        # this connection's loop.
        _LOOP_CONNECTION_EPOCH.set(self._connection_epoch)
        return await super()._connection_task_handler(config=config)

    async def _handle_connection_error(self, error: Exception) -> bool:
        started_under = _LOOP_CONNECTION_EPOCH.get()
        if started_under is not None and started_under != self._connection_epoch:
            logger.info(
                f"{self}: ignoring error from a superseded Gemini Live connection: {error}"
            )
            return False  # upstream then lets the old loop exit; no retry, no count
        return await super()._handle_connection_error(error)

    async def _await_outgoing_connection_stopped(
        self, task: asyncio.Task | None, *, timeout: float = 3.0
    ) -> None:
        """Make sure a connection being replaced has actually stopped before we
        start the next one.

        ``_disconnect()`` (upstream) cancels the outgoing connection's background
        loop with only a best-effort ~1s timeout, and proceeds regardless of
        whether that succeeded. If the loop is still alive when a new connection
        starts, it can still be mid-iteration reading ``self._session`` right as
        this method reassigns it — the connection-epoch guard (see
        ``_handle_connection_error``) makes that harmless if it still happens, but
        giving the outgoing loop a real chance to finish first means it usually
        doesn't happen at all, avoiding the wasted reconnect-and-discard cycle.

        Bounded rather than unconditional: a connection that never stops must
        not block reconnection forever, which would turn a freeze into a
        permanent one.
        """
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except (TimeoutError, asyncio.CancelledError):
            logger.warning(
                f"{self}: outgoing Gemini Live connection did not stop in time"
            )
        except Exception:
            # Whatever it failed with is the outgoing connection's own concern —
            # already routed through its own error handling before we got here.
            pass

    # ------------------------------------------------------------------
    # Cost compaction: trade a reconnect for a cheaper context
    # ------------------------------------------------------------------

    @property
    def _context_compaction_enabled(self) -> bool:
        # A Gemini 2.5 reseed forces a recap utterance (see upstream
        # _create_initial_response), which is too intrusive to pay mid-call for a
        # cost win. On 3.x the reseed is silent, so compaction is free of UX cost.
        return CONTEXT_COMPACTION_TRIGGER_TOKENS > 0 and self._is_gemini_3

    async def _handle_msg_usage_metadata(self, message: LiveServerMessage):
        """Watch reported prompt size and flag compaction once it crosses the trigger."""
        await super()._handle_msg_usage_metadata(message)

        if not self._context_compaction_enabled or self._context_compaction_pending:
            return

        if self._awaiting_context_compaction_seed:
            # Gemini reports the finished turn's usage a moment after we start the
            # refresh, so this figure describes the context we are already
            # discarding. Acting on it would queue a second refresh against the
            # compacted session.
            return

        usage = message.usage_metadata
        prompt_tokens = (usage.prompt_token_count or 0) if usage else 0
        if prompt_tokens >= CONTEXT_COMPACTION_TRIGGER_TOKENS:
            logger.debug(
                f"{self}: context at {prompt_tokens} tokens, over the "
                f"{CONTEXT_COMPACTION_TRIGGER_TOKENS} compaction trigger; "
                "scheduling a session refresh"
            )
            self._context_compaction_pending = True

    def _node_transition_in_flight(self) -> bool:
        return bool(
            self._pending_node_transition_function_calls
            or self._awaiting_node_transition_context
            or (
                self._transition_function_call_task
                and not self._transition_function_call_task.done()
            )
        )

    async def _maybe_compact_context(self) -> None:
        """Refresh the session if it is over budget and this is a safe moment."""
        if not self._context_compaction_pending:
            return

        if self._node_transition_in_flight():
            # A transition already reconnects and reseeds from text, which is the
            # same compaction; let it do the work.
            self._context_compaction_pending = False
            return

        if self._user_is_speaking:
            # Stay connected through the utterance and retry after the next turn.
            return

        if not self._session or self._disconnecting or self._context is None:
            return

        self._context_compaction_pending = False
        await self._compact_context_via_reconnect()

    async def _compact_context_via_reconnect(self) -> None:
        """Reconnect and reseed from LLMContext, dropping accumulated audio.

        The live session keeps spoken history as audio; LLMContext keeps it as
        transcripts. Seeding a fresh session from LLMContext therefore replaces
        the accumulated audio with its much smaller, much cheaper text form, and
        every later turn is billed against that.
        """
        logger.info(f"{self}: compacting Gemini Live context via session refresh")
        self._awaiting_context_compaction_seed = True
        self._session_resumption_handle = None
        outgoing_task = self._connection_task
        await self._disconnect()
        await self._await_outgoing_connection_stopped(outgoing_task)
        await self._connect(session_resumption_handle=None)

    # ------------------------------------------------------------------
    # Frame handling: mute, TTSSpeakFrame, BotStoppedSpeakingFrame flush
    # ------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, UserMuteStartedFrame):
            self._user_is_muted = True
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, UserMuteStoppedFrame):
            self._user_is_muted = False
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSSpeakFrame):
            # Greeting trigger: the engine queues a TTSSpeakFrame to start the
            # bot's first turn after node setup. Gemini Live renders its own
            # audio, so we don't pass the frame through. For configured static
            # text greetings, ask Gemini to say the exact greeting; otherwise
            # re-enter _handle_context to kick off the normal initial response.
            if not self._handled_initial_context:
                greeting_text = frame.text.strip() if frame.text else ""
                if greeting_text:
                    await self._handle_initial_greeting(self._context, greeting_text)
                else:
                    await self._handle_context(self._context)
            else:
                logger.warning(
                    f"{self}: TTSSpeakFrame after initial context already "
                    "handled — Gemini Live owns audio generation, ignoring"
                )
            return
        if isinstance(frame, BotStoppedSpeakingFrame):
            # Belt-and-suspenders: the main drain happens in
            # _set_bot_is_responding(False), but if Gemini delays turn_complete
            # past the audible end of the turn, flushing here ensures a pending
            # node transition fires promptly.
            await self._run_pending_node_transition_function_calls()
            # Fall through to super for the actual push.
        await super().process_frame(frame, direction)

    async def _send_user_audio(self, frame):
        if self._user_is_muted:
            return
        if self._awaiting_context_compaction_seed:
            self._hold_audio_during_compaction(frame)
            return
        await super()._send_user_audio(frame)

    def _hold_audio_during_compaction(self, frame) -> None:
        """Retain user audio while the compaction reconnect is in flight.

        Upstream drops audio whenever the session is down, which would cost the
        user the first word of a reply that happened to land on the refresh.
        Holding it turns that into a short delay instead.
        """
        self._compaction_audio_frames.append(frame)
        self._compaction_audio_bytes += len(frame.audio)

        if self._compaction_audio_bytes <= MAX_COMPACTION_BUFFERED_AUDIO_BYTES:
            return

        logger.warning(
            f"{self}: compaction reconnect still in flight after "
            f"{MAX_COMPACTION_BUFFERED_AUDIO_BYTES} bytes of held audio; "
            "dropping the oldest"
        )
        while (
            self._compaction_audio_bytes > MAX_COMPACTION_BUFFERED_AUDIO_BYTES
            and self._compaction_audio_frames
        ):
            dropped = self._compaction_audio_frames.pop(0)
            self._compaction_audio_bytes -= len(dropped.audio)

    async def _flush_compaction_audio(self) -> None:
        """Replay audio held during the refresh into the fresh session."""
        if not self._compaction_audio_frames:
            return

        frames = self._compaction_audio_frames
        self._compaction_audio_frames = []
        self._compaction_audio_bytes = 0
        logger.debug(
            f"{self}: replaying {len(frames)} user audio frame(s) held during compaction"
        )
        for frame in frames:
            await super()._send_user_audio(frame)

    def _discard_compaction_audio(self) -> None:
        self._compaction_audio_frames = []
        self._compaction_audio_bytes = 0

    # ------------------------------------------------------------------
    # Context lifecycle: Dograh pre-populates self._context via the engine,
    # so upstream's "first arrival === self._context is None" check doesn't
    # work. We gate on _handled_initial_context instead and skip the
    # init-instruction reconciliation (Dograh updates system_instruction at
    # runtime via _update_settings, not via init).
    # ------------------------------------------------------------------

    async def _handle_context(self, context: LLMContext):
        if self._awaiting_node_transition_context:
            self._context = context
            self._node_transition_context_received = True
            await self._maybe_seed_node_transition_context()
            return
        if not self._handled_initial_context:
            self._handled_initial_context = True
            self._context = context
            await self._create_initial_response()
        else:
            self._context = context
            await self._process_completed_function_calls(send_new_results=True)

    async def handle_prerecorded_greeting(
        self, context: LLMContext | None, transcript: str | None
    ) -> None:
        """Open the conversation after a recording has greeted the caller.

        A recorded greeting is played straight to the output transport, so the
        opening TTSSpeakFrame never reaches this service: without this, Gemini
        is never told the call started or what it already said, and its first
        reply greets the caller a second time. Seed the greeting the caller
        heard as a model turn, accept caller audio, and generate nothing.

        ``transcript`` arrives here rather than through LLMContext because the
        recording is still playing; the aggregator commits it only once
        playback drains, after this seed is sent. (Ported from upstream dograh
        9c82ceb0, Gemini part.)
        """
        if self._handled_initial_context:
            return
        if context is None:
            logger.warning(
                f"{self}: received prerecorded greeting before context was set"
            )
            return
        self._handled_initial_context = True
        self._context = context
        # A fresh session never issued the tool calls in this history, so mark
        # their results delivered rather than sending them as tool responses.
        await self._process_completed_function_calls(send_new_results=False)
        self._pending_tool_results.clear()
        await self._create_prerecorded_greeting_response(transcript)

    async def _create_prerecorded_greeting_response(self, transcript: str | None):
        """Seed the spoken greeting, leaving the turn open for the caller.

        ``turn_complete=False`` is what separates this from every other
        opening: Gemini takes the history but is not asked to produce a turn,
        so it waits for the caller instead of greeting them again.
        """
        if self._disconnecting:
            return

        if not self._session:
            self._pending_prerecorded_greeting = (transcript,)
            self._run_llm_when_session_ready = True
            return

        self._pending_prerecorded_greeting = None

        adapter = self.get_llm_adapter()
        turns = list(
            adapter.get_llm_invocation_params(self._context).get("messages", [])
        )
        if transcript:
            turns.append(Content(role="model", parts=[Part(text=transcript)]))
        if not self._is_gemini_3 and (
            not turns or getattr(turns[-1], "role", None) != "user"
        ):
            # Gemini 2.5 requires a seed to end on a user turn; padding one that
            # already does would put two user turns back to back.
            turns.append(Content(role="user", parts=[Part(text=" ")]))

        logger.debug("Seeding Gemini Live with a prerecorded greeting")

        try:
            if turns:
                await self._session.send_client_content(
                    turns=turns, turn_complete=False
                )
        except Exception as e:
            await self._handle_send_error(e)

        if not self._is_gemini_3:
            # 2.5 only picks seeded history up once a turn completes; let the
            # caller's first utterance carry that completion.
            self._needs_initial_turn_complete_message = True

        self._ready_for_realtime_input = True

    async def _handle_initial_greeting(self, context: LLMContext, greeting_text: str):
        """Trigger the first Gemini turn with an exact static text greeting."""
        if context is None:
            logger.warning(
                f"{self}: received initial greeting trigger before context was set"
            )
            return

        self._handled_initial_context = True
        self._context = context
        await self._create_initial_greeting_response(greeting_text)

    async def _create_initial_greeting_response(self, greeting_text: str):
        """Ask Gemini Live to speak the configured greeting exactly once."""
        if self._disconnecting:
            return

        if not self._session:
            self._pending_initial_greeting_text = greeting_text
            self._run_llm_when_session_ready = True
            return

        self._pending_initial_greeting_text = None
        prompt = format_static_greeting_prompt(greeting_text)
        turn = Content(role="user", parts=[Part(text=prompt)])

        logger.debug("Creating Gemini Live initial response from static greeting")

        await self.start_ttfb_metrics()

        try:
            await self._session.send_client_content(
                turns=[turn],
                turn_complete=True,
            )
            # Gemini 3.x also needs a realtime-input nudge to begin inference.
            if self._is_gemini_3:
                await self._session.send_realtime_input(text=" ")
        except Exception as e:
            await self._handle_send_error(e)

        self._ready_for_realtime_input = True

    # ------------------------------------------------------------------
    # Session lifecycle: drop upstream's automatic reconnect-seed and
    # initial-context-seed paths. The TTSSpeakFrame trigger and the
    # function-call-result LLMContextFrame are the only paths that should
    # kick off bot turns in the Dograh flow.
    # ------------------------------------------------------------------

    @traced_gemini_live(operation="llm_setup")
    async def _handle_session_ready(self, session):
        logger.debug(
            f"In _handle_session_ready self._run_llm_when_session_ready: {self._run_llm_when_session_ready}"
        )
        self._session = session
        if self._awaiting_node_transition_context:
            # Do not accept realtime input until the function-call result frame
            # has updated the shared context and that complete history is seeded.
            self._ready_for_realtime_input = False
            await self._maybe_seed_node_transition_context()
            return
        if self._awaiting_context_compaction_seed:
            # Compaction refresh: the context is already complete, so seed it
            # straight away. for_reconnect keeps the seed silent on Gemini 3.x,
            # which is the only place compaction is enabled.
            self._awaiting_context_compaction_seed = False
            self._ready_for_realtime_input = True
            await self._create_initial_response(for_reconnect=True)
            await self._flush_compaction_audio()
            await self._drain_pending_tool_results()
            return
        self._ready_for_realtime_input = True
        if self._run_llm_when_session_ready:
            # Context arrived before session was ready — fulfil the queued
            # initial response now.
            self._run_llm_when_session_ready = False
            if self._pending_prerecorded_greeting is not None:
                (transcript,) = self._pending_prerecorded_greeting
                await self._create_prerecorded_greeting_response(transcript)
            elif self._pending_initial_greeting_text is not None:
                await self._create_initial_greeting_response(
                    self._pending_initial_greeting_text
                )
            else:
                await self._create_initial_response()
        await self._drain_pending_tool_results()
        # Otherwise: no automatic seed. Reconnect after a session-resumption
        # update relies on the server-side restored state; reconnects without
        # a handle (e.g. node transitions before any handle was issued) are
        # followed by a function-call-result LLMContextFrame which feeds the
        # updated-context branch in _handle_context.

    async def _maybe_seed_node_transition_context(self) -> None:
        if (
            not self._awaiting_node_transition_context
            or not self._node_transition_context_received
            or not self._session
            # A node-transition context frame can arrive while the reconnect's
            # disconnect is still in flight: _session still points to the old
            # session being torn down, so `not self._session` above does not yet
            # protect us. Seeding here would run against the dying session and
            # clear the node-transition flags, so the real seed never happens
            # when the fresh session is ready, and every word the caller says
            # after the transition is dropped. Wait until the reconnect settles.
            # (Upstream dograh fa1df112; seen here on run 600.)
            or self._disconnecting
            or self._node_transition_context_seed_started
        ):
            return

        self._node_transition_context_seed_started = True
        try:
            # The complete tool result is already present in the history being
            # seeded, so mark it delivered locally instead of sending a provider
            # tool response for a call that the fresh session never issued.
            await self._process_completed_function_calls(send_new_results=False)
            await self._create_initial_response()
            self._awaiting_node_transition_context = False
            self._node_transition_context_received = False
            await self._drain_pending_tool_results()
        finally:
            self._node_transition_context_seed_started = False
```
## Appendix B: `service_factory.py`, compression wiring for the 3.1 and Vertex branches (`b4315db7`)

The 3.8 branch (section 5.6) already includes the same compression wiring.

```diff
diff --git a/api/services/pipecat/service_factory.py b/api/services/pipecat/service_factory.py
index 569c30b4..50ef9dfb 100644
--- a/api/services/pipecat/service_factory.py
+++ b/api/services/pipecat/service_factory.py
@@ -1125,8 +1125,12 @@ def create_realtime_llm_service(user_config, audio_config: "AudioConfig"):
         )
     elif provider == ServiceProviders.GOOGLE_REALTIME.value:
         from api.services.pipecat.realtime.gemini_live import (
+            COMPRESSION_TRIGGER_TOKENS,
             DograhGeminiLiveLLMService,
         )
+        from pipecat.services.google.gemini_live.llm import (
+            ContextWindowCompressionParams,
+        )
 
         # Gemini Live enables input/output audio transcription by default
         # in its _connect() method — no need to configure it explicitly.
@@ -1134,6 +1138,10 @@ def create_realtime_llm_service(user_config, audio_config: "AudioConfig"):
             "model": model,
             "voice": voice or "Puck",
         }
+        if COMPRESSION_TRIGGER_TOKENS > 0:
+            settings_kwargs["context_window_compression"] = ContextWindowCompressionParams(
+                enabled=True, trigger_tokens=COMPRESSION_TRIGGER_TOKENS
+            )
         if language:
             settings_kwargs["language"] = language
         return DograhGeminiLiveLLMService(
@@ -1141,9 +1149,15 @@ def create_realtime_llm_service(user_config, audio_config: "AudioConfig"):
             settings=DograhGeminiLiveLLMService.Settings(**settings_kwargs),
         )
     elif provider == ServiceProviders.GOOGLE_VERTEX_REALTIME.value:
+        from api.services.pipecat.realtime.gemini_live import (
+            COMPRESSION_TRIGGER_TOKENS,
+        )
         from api.services.pipecat.realtime.gemini_live_vertex import (
             DograhGeminiLiveVertexLLMService,
         )
+        from pipecat.services.google.gemini_live.llm import (
+            ContextWindowCompressionParams,
+        )
 
         project_id = getattr(realtime_config, "project_id", None)
         location = getattr(realtime_config, "location", None) or "us-east4"
@@ -1153,6 +1167,10 @@ def create_realtime_llm_service(user_config, audio_config: "AudioConfig"):
             "model": model,
             "voice": voice or "Charon",
         }
+        if COMPRESSION_TRIGGER_TOKENS > 0:
+            settings_kwargs["context_window_compression"] = ContextWindowCompressionParams(
+                enabled=True, trigger_tokens=COMPRESSION_TRIGGER_TOKENS
+            )
         if language:
             settings_kwargs["language"] = language
         return DograhGeminiLiveVertexLLMService(
```
## Appendix C: `docker-compose.yaml` changes (`83ebf437`, `4721ae7c`)

Both go in the **api** service's `environment:` block.

```diff
diff --git a/docker-compose.yaml b/docker-compose.yaml
index e282ea8c..3af97c7e 100644
--- a/docker-compose.yaml
+++ b/docker-compose.yaml
@@ -159,7 +159,9 @@ services:
     environment:
       # Core application config
       ENVIRONMENT: "${ENVIRONMENT:-local}"
-      LOG_LEVEL: "INFO"
+      # Set LOG_LEVEL=DEBUG in .env to see what Gemini Live hears during a call
+      # ([Transcription:user] lines); it is noisy, so set it back afterwards.
+      LOG_LEVEL: "${LOG_LEVEL:-INFO}"
 
       # Set to "false" in .env to disable public signup (invite-only installs).
       ENABLE_SIGNUP: "${ENABLE_SIGNUP:-true}"
```

```diff
diff --git a/docker-compose.yaml b/docker-compose.yaml
index 09f8bd83..b71cc0f0 100644
--- a/docker-compose.yaml
+++ b/docker-compose.yaml
@@ -268,6 +268,17 @@ services:
       WF_GEN_AZURE_OPENAI_DEPLOYMENT: "${WF_GEN_AZURE_OPENAI_DEPLOYMENT:-}"
       WF_GEN_AZURE_OPENAI_API_VERSION: "${WF_GEN_AZURE_OPENAI_API_VERSION:-}"
 
+      # Gemini Live cost controls. Gemini bills the whole accumulated context
+      # every model turn and holds spoken history as audio, so a long call pays
+      # repeatedly for an expensive form of its own transcript. Refreshing the
+      # session once the context passes the trigger reseeds it from text
+      # instead, at the cost of a ~1.3s reconnect; 5000 suits 3-5 minute calls,
+      # and 0 switches it off. The compression trigger is server-side sliding
+      # window: it rarely fires while compaction holds the context down, but it
+      # lifts Gemini's 15-minute cap on uncompressed audio sessions.
+      GEMINI_LIVE_CONTEXT_COMPACTION_TRIGGER_TOKENS: "${GEMINI_LIVE_CONTEXT_COMPACTION_TRIGGER_TOKENS:-0}"
+      GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS: "${GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS:-16000}"
+
       # Code Editor. The API does not execute anything itself — it calls the
       # handlers container, which owns the sandbox. This URL must be the
       # service name on app-network: the code's own fallback is localhost:8080,
```
## Appendix D: `Dockerfile`, `requirements.txt` and the audio wire-format test (`02d1075a`, `d2f5b40d`)

```diff
diff --git a/api/Dockerfile b/api/Dockerfile
index c1874eaf..dcdc739a 100644
--- a/api/Dockerfile
+++ b/api/Dockerfile
@@ -13,7 +13,10 @@ RUN apt-get update && apt-get install -y \
     && rm -rf /var/lib/apt/lists/*
 
 # uv (https://github.com/astral-sh/uv) for ~5-10x faster installs than pip.
-COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
+# Pinned: a new uv image invalidates every layer below and reinstalls all
+# Python packages at their newest allowed versions (on 2026-09-30 that pulled
+# google-genai 2.25.0 and broke Gemini Live). Bump it deliberately.
+COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /uvx /usr/local/bin/
 
 # Build the venv at the path it will live at in the final image, so shebangs
 # and console-scripts inside the venv reference the correct runtime location
diff --git a/api/requirements.txt b/api/requirements.txt
index 69ad5e17..5515e7a9 100644
--- a/api/requirements.txt
+++ b/api/requirements.txt
@@ -23,3 +23,4 @@ PyNaCl==1.6.2
 tzdata==2026.3
 openai==2.32.0
 vakyamai==0.1.8
+google-genai==2.24.0
```

```diff
diff --git a/api/Dockerfile b/api/Dockerfile
index dcdc739a..a701464a 100644
--- a/api/Dockerfile
+++ b/api/Dockerfile
@@ -26,9 +26,12 @@ ENV VIRTUAL_ENV=/opt/venv \
 RUN python -m venv "$VIRTUAL_ENV"
 
 # Layer 1: API deps. Cache invalidates only when requirements.txt changes.
+# api/constraints.txt pins every package (pipecat's too) to a tested version,
+# so a rebuild can't silently upgrade anything.
 RUN --mount=type=bind,source=api/requirements.txt,target=/tmp/req.txt \
+    --mount=type=bind,source=api/constraints.txt,target=/tmp/constraints.txt \
     --mount=type=cache,target=/root/.cache/uv \
-    uv pip install -r /tmp/req.txt
+    uv pip install -r /tmp/req.txt -c /tmp/constraints.txt
 
 # Layer 2: pipecat deps. Cache invalidates when pipecat source changes.
 # After installing pipecat, two hardening tweaks:
@@ -39,10 +42,11 @@ RUN --mount=type=bind,source=api/requirements.txt,target=/tmp/req.txt \
 #      doesn't hit the network on first agent run. NLTK auto-finds it under
 #      sys.prefix/nltk_data, so it travels with the venv on COPY.
 RUN --mount=type=bind,source=pipecat,target=/tmp/pipecat,rw \
+    --mount=type=bind,source=api/constraints.txt,target=/tmp/constraints.txt \
     --mount=type=cache,target=/root/.cache/uv \
-    uv pip install '/tmp/pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb,mcp,inworld,smallest]' \
+    uv pip install '/tmp/pipecat[cartesia,deepgram,openai,elevenlabs,groq,google,azure,sarvam,soundfile,silero,webrtc,speechmatics,openrouter,camb,mcp,inworld,smallest]' -c /tmp/constraints.txt \
  && uv pip uninstall opencv-python \
- && uv pip install opencv-python-headless \
+ && uv pip install opencv-python-headless -c /tmp/constraints.txt \
  && python -c "import nltk; nltk.download('punkt_tab', download_dir='/opt/venv/nltk_data', quiet=True)"
 
 # Layer 3: Rumik TTS plugin, installed AFTER pipecat and with --no-deps.
```

### `api/tests/test_gemini_live_audio_wire_format.py` (new)

This test fails if a future `google-genai` changes how caller audio is labelled. That is exactly what broke speech recognition on 2026-09-30.

```python
"""Gemini Live must receive the caller's audio labelled ``mimeType``.

On 2026-09-30 a rebuild pulled google-genai 2.25.0 (the pipecat extra only
bounds it ``<3``), which sends ``mime_type`` instead, and Gemini Live stopped
transcribing callers in prod. ``api/requirements.txt`` pins the version; this
fails if a future bump changes the wire format again.
"""

import asyncio
import json

from google.genai import _api_client, live, types


def test_realtime_audio_is_sent_with_camel_case_mime_type():
    sent = []

    class _Socket:
        async def send(self, message):
            sent.append(json.loads(message))

    session = live.AsyncSession(
        api_client=_api_client.BaseApiClient(api_key="test"), websocket=_Socket()
    )
    asyncio.run(
        session.send_realtime_input(
            audio=types.Blob(data=b"\x00\x01", mime_type="audio/pcm;rate=16000")
        )
    )

    assert sent == [
        {
            "realtime_input": {
                "audio": {"data": "AAE=", "mimeType": "audio/pcm;rate=16000"}
            }
        }
    ]
```
## Appendix E: complete `api/constraints.txt`

This is the package set as it resolved on 17 Sep 2026, for Linux / Python 3.13. Use it as-is only if the target's `requirements.txt` and pipecat extras match ours. Otherwise regenerate it from a known-good container with `docker compose exec -T api pip freeze > api/constraints.txt`, and keep `google-genai==2.24.0`.

```text
# Every Python package in the API image, pinned to what prod ran before
# 2026-09-30, when a rebuild upgraded 32 unpinned packages at once and broke
# Gemini Live calls. Both install steps in api/Dockerfile use this file, so a
# rebuild installs exactly these versions. Upgrade a package deliberately:
# change its line here, rebuild, and test a call.

aioboto3==15.1.0
aiobotocore==2.24.0
aiofile==3.12.3
aiofiles==25.1.0
aiohappyeyeballs==2.7.1
aiohttp-retry==2.9.1
aiohttp==3.14.3
aioice==0.10.2
aioitertools==0.13.0
aiortc==1.15.0
aiosignal==1.4.0
alembic-postgresql-enum==1.8.0
alembic==1.16.5
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
argon2-cffi-bindings==26.1.0
argon2-cffi==25.1.0
arq==0.26.3
asyncpg==0.30.0
attrs==26.1.0
audioop-lts==0.2.2
authlib==1.8.0
av==17.1.0
azure-cognitiveservices-speech==1.51.2
azure-core==1.41.0
backoff==2.2.1
bcrypt==5.0.0
beartype==0.22.9
boto3==1.39.11
botocore==1.39.11
cachetools==7.2.0
caio==0.12.4
camb-sdk==1.6.1
certifi==2026.7.22
cffi==2.1.1
charset-normalizer==3.5.1
click==8.5.0
cloudpickle==3.1.2
cryptography==50.0.1
cyclopts==4.25.2
deepgram-sdk==7.9.0
defusedxml==0.7.1
distro==1.9.0
dnspython==2.8.0
docopt==0.6.2
docstring-parser==0.18.0
email-validator==2.3.0
exceptiongroup==1.3.1
fastapi==0.135.3
fastmcp==3.2.4
filelock==4.0.0
flatbuffers==25.12.19
frozenlist==1.8.0
fsspec==2026.7.0
google-api-core==2.37.0
google-auth==2.58.0
google-cloud-speech==2.40.0
google-cloud-texttospeech==2.37.0
google-crc32c==1.8.0
google-genai==2.24.0
googleapis-common-protos==1.75.3
greenlet==3.5.6
griffelib==2.3.0
groq==1.7.0
grpcio-status==1.84.0
grpcio==1.84.0
h11==0.16.0
hf-xet==1.6.0
hiredis==3.4.1
httpcore==1.0.9
httpx-sse==0.4.3
httpx==0.28.1
huggingface-hub==0.36.2
idna==3.19
ifaddr==0.2.0
jaraco-classes==3.4.0
jaraco-context==6.1.2
jaraco-functools==4.6.0
jeepney==0.9.0
jiter==0.17.0
jmespath==1.1.0
joblib==1.6.0
joserfc==1.7.5
jsonref==1.1.0
jsonschema-path==0.5.0
jsonschema-specifications==2025.9.1
jsonschema==4.26.0
keyring==25.7.0
langfuse==3.9.3
llvmlite==0.49.0
loguru==0.7.3
mako==1.4.1
markdown-it-py==4.2.0
markdown==3.10.3
markupsafe==3.0.3
mcp==1.30.0
mdurl==0.1.2
minio==7.2.16
more-itertools==11.1.0
mpmath==1.3.0
msgpack==1.1.2
multidict==6.8.0
nltk==3.10.3
num2words==0.5.14
numba==0.67.0
numpy==2.5.3
onnxruntime==1.24.4
openai==2.32.0
openapi-pydantic==0.5.1
opencv-python-headless==5.0.0.93
opencv-python==4.14.0.94
opentelemetry-api==1.44.0
opentelemetry-exporter-otlp-proto-common==1.44.0
opentelemetry-exporter-otlp-proto-http==1.44.0
opentelemetry-proto==1.44.0
opentelemetry-sdk==1.44.0
opentelemetry-semantic-conventions==0.65b0
packaging==25.0
pathable==0.6.0
pgvector==0.4.2
pillow==12.3.0
platformdirs==4.11.9
posthog==7.19.1
propcache==0.5.4
proto-plus==1.28.4
protobuf==6.33.6
py-key-value-aio==0.4.5
pyasn1-modules==0.4.2
pyasn1==0.6.4
pycparser==3.0
pycryptodome==3.23.0
pydantic-core==2.46.5
pydantic-settings==2.15.0
pydantic==2.13.5
pyee==14.0.0
pygments==2.21.0
pyjwt==2.14.0
pylibsrtp==1.0.0
pyloudnorm==0.2.0
pynacl==1.6.2
pyopenssl==26.4.0
pyperclip==1.11.0
python-dateutil==2.9.0.post0
python-dotenv==1.2.3
python-multipart==0.0.27
pyyaml-include==1.4.1
pyyaml==6.0.3
redis==5.3.1
referencing==0.37.0
regex==2026.9.10
requests==2.34.2
resampy==0.4.3
rich-rst==2.1.0
rich==15.0.0
rpds-py==2026.6.3
s3transfer==0.13.1
safetensors==0.8.0
sarvamai==0.1.28
scipy==1.18.1
secretstorage==3.5.0
sentry-sdk==2.38.0
shellingham==1.5.4
six==1.17.0
sniffio==1.3.1
sounddevice==0.5.6
soundfile==0.13.1
soxr==1.0.0
speechmatics-rt==1.1.1
speechmatics-voice==0.2.8
sqlalchemy==2.0.43
sse-starlette==3.4.11
starlette==1.6.0
sympy==1.14.0
tenacity==9.1.4
tokenizers==0.22.2
tqdm==4.70.1
transformers==4.57.6
tuner-pipecat-sdk==0.2.4
twilio==9.8.0
typer==0.27.2
typing-extensions==4.16.0
typing-inspection==0.4.4
tzdata==2026.3
uncalled-for==0.4.0
urllib3==2.8.0
uvicorn==0.35.0
vakyamai==0.1.8
watchfiles==1.2.0
websocket-client==1.9.2
websockets==15.0.1
wrapt==1.17.3
yarl==1.25.1
```
## Appendix F: tests for compaction and the reconnect race

### `api/tests/test_gemini_live_context_compaction.py` (new, `b4315db7`)

```python
"""Context compaction: refreshing the Gemini Live session to shed audio history.

Gemini Live bills the whole accumulated context every turn and keeps spoken
history as audio, so a long call pays repeatedly for an expensive form of its
own transcript. Reconnecting reseeds from LLMContext (text), so these tests pin
down when that refresh fires and when it must not.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService

from api.services.pipecat.realtime import gemini_live as gemini_live_module
from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService

GEMINI_3_MODEL = "models/gemini-3.1-flash-live-preview"
GEMINI_25_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"

TRIGGER = 5000


@pytest.fixture(autouse=True)
def _fixed_trigger(monkeypatch):
    """Pin the trigger so tests don't depend on the deployment's env value."""
    monkeypatch.setattr(
        gemini_live_module, "CONTEXT_COMPACTION_TRIGGER_TOKENS", TRIGGER
    )


class _TestDograhGeminiLiveLLMService(DograhGeminiLiveLLMService):
    """Dograh Gemini service with client creation stubbed for unit tests."""

    def create_client(self):
        self._client = SimpleNamespace(
            aio=SimpleNamespace(live=SimpleNamespace(connect=None))
        )


class _FakeSession:
    def __init__(self):
        self.send_client_content = AsyncMock()
        self.send_tool_response = AsyncMock()
        self.send_realtime_input = AsyncMock()
        self.close = AsyncMock()


def _make_service(model: str = GEMINI_3_MODEL) -> _TestDograhGeminiLiveLLMService:
    service = _TestDograhGeminiLiveLLMService(
        api_key="test-key",
        settings=_TestDograhGeminiLiveLLMService.Settings(model=model),
    )
    service.stop_all_metrics = AsyncMock()
    service.start_ttfb_metrics = AsyncMock()
    service.start_llm_usage_metrics = AsyncMock()
    service.cancel_task = AsyncMock()
    service.push_error = AsyncMock()
    return service


def _audio_frame(audio: bytes) -> SimpleNamespace:
    return SimpleNamespace(audio=audio, sample_rate=16000, num_channels=1)


def _usage_message(prompt_tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens,
            response_token_count=10,
            total_token_count=prompt_tokens + 10,
            cached_content_token_count=None,
            thoughts_token_count=None,
        )
    )


# ----------------------------------------------------------------------
# Deciding when the context is over budget
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_over_trigger_schedules_compaction():
    service = _make_service()

    await service._handle_msg_usage_metadata(_usage_message(TRIGGER + 1))

    assert service._context_compaction_pending


@pytest.mark.asyncio
async def test_usage_under_trigger_leaves_session_alone():
    service = _make_service()

    await service._handle_msg_usage_metadata(_usage_message(TRIGGER - 1))

    assert not service._context_compaction_pending


@pytest.mark.asyncio
async def test_usage_metrics_still_reported():
    """Compaction piggybacks on usage reporting; it must not swallow it."""
    service = _make_service()

    await service._handle_msg_usage_metadata(_usage_message(TRIGGER + 1))

    service.start_llm_usage_metrics.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_usage_during_a_refresh_does_not_requeue():
    """The finished turn's usage lands ~120ms into the refresh, describing the
    context being discarded. Acting on it queues a redundant second refresh."""
    service = _make_service()
    service._awaiting_context_compaction_seed = True

    await service._handle_msg_usage_metadata(_usage_message(TRIGGER * 2))

    assert not service._context_compaction_pending


@pytest.mark.asyncio
async def test_usage_after_the_refresh_completes_still_arms():
    service = _make_service()
    service._awaiting_context_compaction_seed = False

    await service._handle_msg_usage_metadata(_usage_message(TRIGGER + 1))

    assert service._context_compaction_pending


@pytest.mark.asyncio
async def test_compaction_disabled_on_gemini_25():
    """A 2.5 reseed forces a recap utterance, so compaction must not run there."""
    service = _make_service(model=GEMINI_25_MODEL)

    await service._handle_msg_usage_metadata(_usage_message(TRIGGER * 10))

    assert not service._context_compaction_pending


@pytest.mark.asyncio
async def test_compaction_disabled_when_trigger_is_zero(monkeypatch):
    monkeypatch.setattr(gemini_live_module, "CONTEXT_COMPACTION_TRIGGER_TOKENS", 0)
    service = _make_service()

    await service._handle_msg_usage_metadata(_usage_message(100_000))

    assert not service._context_compaction_pending


# ----------------------------------------------------------------------
# Choosing a safe moment to act
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_waits_while_user_is_speaking():
    service = _make_service()
    service._session = _FakeSession()
    service._context = LLMContext(messages=[])
    service._context_compaction_pending = True
    service._user_is_speaking = True
    service._compact_context_via_reconnect = AsyncMock()

    await service._maybe_compact_context()

    service._compact_context_via_reconnect.assert_not_awaited()
    # Still pending, so the next bot turn retries rather than dropping it.
    assert service._context_compaction_pending


@pytest.mark.asyncio
async def test_compaction_yields_to_a_pending_node_transition():
    """A transition reconnects and reseeds from text already — same compaction."""
    service = _make_service()
    service._session = _FakeSession()
    service._context = LLMContext(messages=[])
    service._context_compaction_pending = True
    service._awaiting_node_transition_context = True
    service._compact_context_via_reconnect = AsyncMock()

    await service._maybe_compact_context()

    service._compact_context_via_reconnect.assert_not_awaited()
    assert not service._context_compaction_pending


@pytest.mark.asyncio
async def test_compaction_skipped_while_disconnecting():
    service = _make_service()
    service._session = _FakeSession()
    service._context = LLMContext(messages=[])
    service._context_compaction_pending = True
    service._disconnecting = True
    service._compact_context_via_reconnect = AsyncMock()

    await service._maybe_compact_context()

    service._compact_context_via_reconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_turn_end_runs_a_due_compaction():
    service = _make_service()
    service._session = _FakeSession()
    service._context = LLMContext(messages=[])
    service._bot_is_responding = True
    service._context_compaction_pending = True
    service._compact_context_via_reconnect = AsyncMock()

    await service._set_bot_is_responding(False)

    service._compact_context_via_reconnect.assert_awaited_once()
    assert not service._context_compaction_pending


# ----------------------------------------------------------------------
# Performing the refresh
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_drops_the_resumption_handle_and_reconnects():
    """Resuming would restore the audio history we are trying to shed."""
    service = _make_service()
    service._session = _FakeSession()
    service._session_resumption_handle = "stale-handle"
    service._disconnect = AsyncMock()
    service._connect = AsyncMock()

    await service._compact_context_via_reconnect()

    assert service._awaiting_context_compaction_seed
    assert service._session_resumption_handle is None
    service._disconnect.assert_awaited_once()
    service._connect.assert_awaited_once_with(session_resumption_handle=None)


@pytest.mark.asyncio
async def test_session_ready_reseeds_from_context_without_speaking():
    service = _make_service()
    service._awaiting_context_compaction_seed = True
    service._create_initial_response = AsyncMock()
    service._drain_pending_tool_results = AsyncMock()

    session = _FakeSession()
    await service._handle_session_ready(session)

    service._create_initial_response.assert_awaited_once_with(for_reconnect=True)
    assert service._session is session
    assert service._ready_for_realtime_input
    assert not service._awaiting_context_compaction_seed


@pytest.mark.asyncio
async def test_held_audio_is_replayed_after_the_seed():
    service = _make_service()
    service._awaiting_context_compaction_seed = True
    service._create_initial_response = AsyncMock()
    service._drain_pending_tool_results = AsyncMock()

    # Speech landing mid-refresh must survive it.
    await service._send_user_audio(_audio_frame(b"\x01\x02"))
    await service._send_user_audio(_audio_frame(b"\x03\x04"))
    assert len(service._compaction_audio_frames) == 2

    sent = []
    monkey_super = AsyncMock(side_effect=lambda frame: sent.append(frame.audio))
    with patch.object(GeminiLiveLLMService, "_send_user_audio", monkey_super):
        await service._handle_session_ready(_FakeSession())

    assert sent == [b"\x01\x02", b"\x03\x04"]
    assert service._compaction_audio_frames == []
    assert service._compaction_audio_bytes == 0


@pytest.mark.asyncio
async def test_held_audio_is_bounded_when_a_reconnect_stalls():
    service = _make_service()
    service._awaiting_context_compaction_seed = True

    chunk = b"\x00" * 4000
    for _ in range(60):  # 240 kB, far past the ~160 kB cap
        await service._send_user_audio(_audio_frame(chunk))

    assert (
        service._compaction_audio_bytes
        <= gemini_live_module.MAX_COMPACTION_BUFFERED_AUDIO_BYTES
    )
    # Oldest dropped, most recent speech kept.
    assert service._compaction_audio_frames


@pytest.mark.asyncio
async def test_muted_audio_is_not_held():
    service = _make_service()
    service._awaiting_context_compaction_seed = True
    service._user_is_muted = True

    await service._send_user_audio(_audio_frame(b"\x01\x02"))

    assert service._compaction_audio_frames == []


@pytest.mark.asyncio
async def test_node_transition_seed_takes_precedence_over_compaction_seed():
    """Both flags set: the transition path owns the reseed and stays gated."""
    service = _make_service()
    service._awaiting_node_transition_context = True
    service._awaiting_context_compaction_seed = True
    service._maybe_seed_node_transition_context = AsyncMock()
    service._create_initial_response = AsyncMock()

    await service._handle_session_ready(_FakeSession())

    service._maybe_seed_node_transition_context.assert_awaited_once()
    service._create_initial_response.assert_not_awaited()
    assert not service._ready_for_realtime_input
```

### `api/tests/test_gemini_live_reconnect_race.py` (new, `a66dd0ba`)

```python
"""The stale-connection race behind the run-442 incident.

``_disconnect()`` cancels the outgoing connection's background receive loop
with only a best-effort ~1s timeout (upstream `cancel_task`), and proceeds
regardless of whether that succeeded. If the loop is still alive when the next
connection starts — as happened repeatedly on a live Vectus Smart Care call
that ran past the compaction threshold — its next iteration reads
``self._session`` after it has already been cleared or reassigned by the
newer connection. That produced ``'NoneType' object has no attribute
'receive'``, which the loop's own error handling treated as a *real* failure
and reconnected for again, discarding the newer connection that was already
under way, then getting stuck repeating that same collision every 15–40
seconds while the caller heard nothing — for most of a 10-minute call.

The fix is a connection "epoch", held in Dograh's subclass
(`api/services/pipecat/realtime/gemini_live.py`) so it ships without a pipecat
change: ``_disconnect()`` bumps it immediately, and a connection's receive loop
captures its own epoch at the start of its life (in a context variable, which
is private to that loop's task).
An error that arrives after that number has moved on belongs to a connection
nobody is using any more and is dropped instead of triggering another
reconnect. These tests reproduce the exact timing (a receive loop blocked mid
turn, exactly where a live connection sits between messages) rather than
asserting on the epoch counter directly, so they fail if the guard is removed
or misplaced, not just if its bookkeeping changes shape.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService

GEMINI_3_MODEL = "models/gemini-3.1-flash-live-preview"


class _TestDograhGeminiLiveLLMService(DograhGeminiLiveLLMService):
    """Dograh Gemini service with client creation stubbed for unit tests."""

    def create_client(self):
        self._client = SimpleNamespace(
            aio=SimpleNamespace(live=SimpleNamespace(connect=None))
        )


def _make_service() -> _TestDograhGeminiLiveLLMService:
    service = _TestDograhGeminiLiveLLMService(
        api_key="test-key",
        settings=_TestDograhGeminiLiveLLMService.Settings(model=GEMINI_3_MODEL),
    )
    service.stop_all_metrics = AsyncMock()
    service.start_ttfb_metrics = AsyncMock()
    service.start_llm_usage_metrics = AsyncMock()
    service.push_error = AsyncMock()
    return service


class _BlockedReceive:
    """A fake ``session.receive()``: sits exactly where a real connection sits
    between turns (awaiting the next message), then fails once released —
    standing in for whatever eventually goes wrong with a superseded
    connection's dangling stream (a closed socket, a read on a cleared
    session, and so on; the exact upstream exception is not the point here).
    """

    def __init__(self, released: asyncio.Event, reached: asyncio.Event):
        self._released = released
        self._reached = reached

    async def receive(self):
        self._reached.set()
        await self._released.wait()
        raise ConnectionResetError("simulated: the underlying connection is gone")
        yield  # pragma: no cover - unreachable; makes this an async generator


class _ConnectContextManager:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False


def _wire_stuck_connection(service) -> tuple[asyncio.Event, asyncio.Event]:
    """Point the service at a connection whose receive loop blocks until the
    test releases it. Returns (released, reached) events."""
    released = asyncio.Event()
    reached = asyncio.Event()
    session = _BlockedReceive(released, reached)
    service._client.aio.live.connect = lambda **kwargs: _ConnectContextManager(session)
    return released, reached


async def _start_connection_task(service) -> asyncio.Task:
    task = asyncio.ensure_future(
        service._connection_task_handler(config=SimpleNamespace())
    )
    return task


@pytest.mark.asyncio
async def test_a_superseded_connections_late_failure_is_not_treated_as_a_new_error():
    """The exact run-442 shape: a disconnect (for an unrelated reason — a
    compaction refresh, a node transition) has already moved the connection
    on by the time this old loop's blocked read finally fails. It must stand
    down rather than reconnect again on top of whatever came after it.
    """
    service = _make_service()
    released, reached = _wire_stuck_connection(service)
    # _handle_connection_error is left real: it holds the guard. Only the
    # consequences a stale error must not have are observed.
    service._reconnect = AsyncMock()

    task = await _start_connection_task(service)
    await asyncio.wait_for(reached.wait(), timeout=1.0)

    # A disconnect happens now, for its own reasons — exactly what
    # _compact_context_via_reconnect / _reconnect_for_node_transition do.
    service._connection_epoch += 1
    service._session = None

    released.set()
    await asyncio.wait_for(task, timeout=1.0)

    service._reconnect.assert_not_awaited()
    service.push_error.assert_not_awaited()  # no "failed after 3 attempts"
    assert service._consecutive_failures == 0  # not counted as a failure


@pytest.mark.asyncio
async def test_a_still_current_connections_failure_still_reconnects_normally():
    """The fix must not suppress real error handling: a connection that has
    NOT been superseded reconnects on failure exactly as before.
    """
    service = _make_service()
    released, reached = _wire_stuck_connection(service)
    service._reconnect = AsyncMock()

    task = await _start_connection_task(service)
    await asyncio.wait_for(reached.wait(), timeout=1.0)

    # No disconnect happens this time — same connection, same epoch.
    released.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert service._consecutive_failures == 1  # counted, as before
    service._reconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_advances_the_epoch_before_anything_else_can_run():
    """_disconnect() must mark the outgoing connection stale as one of its
    first acts — before any of its own awaits (stop_all_metrics,
    cancel_task, session.close) can hand control back to that connection's
    still-running loop.
    """
    service = _make_service()
    service.cancel_task = AsyncMock()
    before = service._connection_epoch

    await service._disconnect()

    # Advanced, not "+1": with the guard also present in pipecat, both bump.
    assert service._connection_epoch > before


# ----------------------------------------------------------------------
# Dograh's own belt-and-suspenders: actually wait for the outgoing
# connection to finish before starting the next one, instead of trusting
# _disconnect()'s best-effort cancellation.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_await_outgoing_connection_stopped_does_nothing_without_a_task():
    service = _make_service()

    await service._await_outgoing_connection_stopped(None)  # must not raise


@pytest.mark.asyncio
async def test_await_outgoing_connection_stopped_returns_once_the_task_finishes():
    service = _make_service()
    still_running = asyncio.Event()

    async def _slow_shutdown():
        await still_running.wait()

    task = asyncio.ensure_future(_slow_shutdown())
    waiter = asyncio.ensure_future(service._await_outgoing_connection_stopped(task))
    await asyncio.sleep(0.01)
    assert not waiter.done()  # genuinely waiting, not a no-op

    still_running.set()
    await asyncio.wait_for(waiter, timeout=1.0)
    assert task.done()


@pytest.mark.asyncio
async def test_await_outgoing_connection_stopped_gives_up_after_a_bound_and_proceeds():
    """A connection that never actually stops must not block reconnection
    forever — that would turn a freeze into a permanent one."""
    service = _make_service()

    task = asyncio.ensure_future(asyncio.sleep(10))
    try:
        await service._await_outgoing_connection_stopped(
            task, timeout=0.02
        )  # must not raise
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_compaction_waits_for_the_outgoing_connection_before_reconnecting():
    """The two calls must be strictly ordered: the old connection's shutdown
    is given a real chance to finish before the new one starts, not raced."""
    service = _make_service()
    order: list[str] = []
    still_running = asyncio.Event()

    async def _slow_shutdown():
        await still_running.wait()

    service._connection_task = asyncio.ensure_future(_slow_shutdown())
    service._session_resumption_handle = "stale"

    async def _disconnect():
        order.append("disconnect")

    async def _connect(**kwargs):
        order.append("connect")

    service._disconnect = _disconnect
    service._connect = _connect

    run = asyncio.ensure_future(service._compact_context_via_reconnect())
    await asyncio.sleep(0.01)
    assert order == ["disconnect"]  # connect must not have started yet

    still_running.set()
    await asyncio.wait_for(run, timeout=1.0)
    assert order == ["disconnect", "connect"]
```
## Appendix G: the `gemini_live.py` diffs, one commit at a time

Use these instead of Appendix A when the target's `gemini_live.py` has its own changes you must keep.

### G.1 Session refresh / compaction (`b4315db7`)

```diff
diff --git a/api/services/pipecat/realtime/gemini_live.py b/api/services/pipecat/realtime/gemini_live.py
index 6b85a4a4..476f010d 100644
--- a/api/services/pipecat/realtime/gemini_live.py
+++ b/api/services/pipecat/realtime/gemini_live.py
@@ -19,9 +19,10 @@ Layers Dograh engine integration quirks onto upstream-pristine
 """
 
 import asyncio
+import os
 from typing import Any
 
-from google.genai.types import Content, Part
+from google.genai.types import Content, LiveServerMessage, Part
 from loguru import logger
 
 from api.services.pipecat.gemini_json_schema_adapter import (
@@ -41,6 +42,32 @@ from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
 from pipecat.services.llm_service import FunctionCallFromLLM
 from pipecat.utils.tracing.service_decorators import traced_gemini_live
 
+# Gemini Live bills the whole accumulated context on every model turn, and holds
+# spoken history in the session as audio — far more tokens than the equivalent
+# transcript, at four times the per-token price. A reconnect reseeds the fresh
+# session from LLMContext, which holds text, so refreshing the session once the
+# context passes this many tokens trades one reconnect for a much cheaper
+# context from there on.
+#
+# Off by default: the refresh costs a reconnect (~1s on this pipeline), during
+# which the user hears no difference but the bot is not listening. Measure that
+# gap on a test call before turning it on, and pick a trigger high enough that it
+# fires once on a long call rather than every few turns.
+CONTEXT_COMPACTION_TRIGGER_TOKENS = int(
+    os.getenv("GEMINI_LIVE_CONTEXT_COMPACTION_TRIGGER_TOKENS", "0")
+)
+
+# Cap on user audio held during a compaction refresh. Sized well past the
+# expected reconnect so a stalled one sheds the oldest audio instead of growing
+# without bound. ~5s of 16 kHz mono PCM.
+MAX_COMPACTION_BUFFERED_AUDIO_BYTES = 16_000 * 2 * 5
+
+# Server-side sliding-window compression. Bounds growth between compactions and
+# lifts Gemini's 15-minute cap on uncompressed audio sessions. 0 disables.
+COMPRESSION_TRIGGER_TOKENS = int(
+    os.getenv("GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS", "16000")
+)
+
 
 class DograhGeminiLiveLLMService(GeminiLiveLLMService):
     """Gemini Live with Dograh engine integration quirks. See module docstring."""
@@ -78,6 +105,17 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
         self._awaiting_node_transition_context: bool = False
         self._node_transition_context_received: bool = False
         self._node_transition_context_seed_started: bool = False
+        # Cost compaction: set when the reported prompt size crosses the trigger,
+        # acted on at the next safe point (bot turn ended, user not speaking).
+        # Stays set until a compaction actually runs, so a skipped turn is
+        # retried rather than dropped.
+        self._context_compaction_pending: bool = False
+        self._awaiting_context_compaction_seed: bool = False
+        # User audio captured while the compaction reconnect is in flight.
+        # Upstream discards audio whenever the session is down, so we hold it
+        # here and replay it once the fresh session is seeded.
+        self._compaction_audio_frames: list[Any] = []
+        self._compaction_audio_bytes: int = 0
 
     # ------------------------------------------------------------------
     # Hooks from upstream GeminiLiveLLMService
@@ -100,6 +138,7 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
         if self._transition_function_call_task:
             await self.cancel_task(self._transition_function_call_task)
             self._transition_function_call_task = None
+        self._discard_compaction_audio()
         await super().cleanup()
 
     async def _handle_changed_settings(self, changed: dict[str, Any]) -> set[str]:
@@ -201,6 +240,7 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
         await super()._set_bot_is_responding(responding)
         if was_responding and not responding:
             await self._run_pending_node_transition_function_calls()
+            await self._maybe_compact_context()
 
     async def _run_pending_node_transition_function_calls(self):
         """Run any node-transition calls deferred during the bot's last turn."""
@@ -230,6 +270,86 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
         await self._disconnect()
         await self._connect(session_resumption_handle=None)
 
+    # ------------------------------------------------------------------
+    # Cost compaction: trade a reconnect for a cheaper context
+    # ------------------------------------------------------------------
+
+    @property
+    def _context_compaction_enabled(self) -> bool:
+        # A Gemini 2.5 reseed forces a recap utterance (see upstream
+        # _create_initial_response), which is too intrusive to pay mid-call for a
+        # cost win. On 3.x the reseed is silent, so compaction is free of UX cost.
+        return CONTEXT_COMPACTION_TRIGGER_TOKENS > 0 and self._is_gemini_3
+
+    async def _handle_msg_usage_metadata(self, message: LiveServerMessage):
+        """Watch reported prompt size and flag compaction once it crosses the trigger."""
+        await super()._handle_msg_usage_metadata(message)
+
+        if not self._context_compaction_enabled or self._context_compaction_pending:
+            return
+
+        if self._awaiting_context_compaction_seed:
+            # Gemini reports the finished turn's usage a moment after we start the
+            # refresh, so this figure describes the context we are already
+            # discarding. Acting on it would queue a second refresh against the
+            # compacted session.
+            return
+
+        usage = message.usage_metadata
+        prompt_tokens = (usage.prompt_token_count or 0) if usage else 0
+        if prompt_tokens >= CONTEXT_COMPACTION_TRIGGER_TOKENS:
+            logger.debug(
+                f"{self}: context at {prompt_tokens} tokens, over the "
+                f"{CONTEXT_COMPACTION_TRIGGER_TOKENS} compaction trigger; "
+                "scheduling a session refresh"
+            )
+            self._context_compaction_pending = True
+
+    def _node_transition_in_flight(self) -> bool:
+        return bool(
+            self._pending_node_transition_function_calls
+            or self._awaiting_node_transition_context
+            or (
+                self._transition_function_call_task
+                and not self._transition_function_call_task.done()
+            )
+        )
+
+    async def _maybe_compact_context(self) -> None:
+        """Refresh the session if it is over budget and this is a safe moment."""
+        if not self._context_compaction_pending:
+            return
+
+        if self._node_transition_in_flight():
+            # A transition already reconnects and reseeds from text, which is the
+            # same compaction; let it do the work.
+            self._context_compaction_pending = False
+            return
+
+        if self._user_is_speaking:
+            # Stay connected through the utterance and retry after the next turn.
+            return
+
+        if not self._session or self._disconnecting or self._context is None:
+            return
+
+        self._context_compaction_pending = False
+        await self._compact_context_via_reconnect()
+
+    async def _compact_context_via_reconnect(self) -> None:
+        """Reconnect and reseed from LLMContext, dropping accumulated audio.
+
+        The live session keeps spoken history as audio; LLMContext keeps it as
+        transcripts. Seeding a fresh session from LLMContext therefore replaces
+        the accumulated audio with its much smaller, much cheaper text form, and
+        every later turn is billed against that.
+        """
+        logger.info(f"{self}: compacting Gemini Live context via session refresh")
+        self._awaiting_context_compaction_seed = True
+        self._session_resumption_handle = None
+        await self._disconnect()
+        await self._connect(session_resumption_handle=None)
+
     # ------------------------------------------------------------------
     # Frame handling: mute, TTSSpeakFrame, BotStoppedSpeakingFrame flush
     # ------------------------------------------------------------------
@@ -273,8 +393,54 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
     async def _send_user_audio(self, frame):
         if self._user_is_muted:
             return
+        if self._awaiting_context_compaction_seed:
+            self._hold_audio_during_compaction(frame)
+            return
         await super()._send_user_audio(frame)
 
+    def _hold_audio_during_compaction(self, frame) -> None:
+        """Retain user audio while the compaction reconnect is in flight.
+
+        Upstream drops audio whenever the session is down, which would cost the
+        user the first word of a reply that happened to land on the refresh.
+        Holding it turns that into a short delay instead.
+        """
+        self._compaction_audio_frames.append(frame)
+        self._compaction_audio_bytes += len(frame.audio)
+
+        if self._compaction_audio_bytes <= MAX_COMPACTION_BUFFERED_AUDIO_BYTES:
+            return
+
+        logger.warning(
+            f"{self}: compaction reconnect still in flight after "
+            f"{MAX_COMPACTION_BUFFERED_AUDIO_BYTES} bytes of held audio; "
+            "dropping the oldest"
+        )
+        while (
+            self._compaction_audio_bytes > MAX_COMPACTION_BUFFERED_AUDIO_BYTES
+            and self._compaction_audio_frames
+        ):
+            dropped = self._compaction_audio_frames.pop(0)
+            self._compaction_audio_bytes -= len(dropped.audio)
+
+    async def _flush_compaction_audio(self) -> None:
+        """Replay audio held during the refresh into the fresh session."""
+        if not self._compaction_audio_frames:
+            return
+
+        frames = self._compaction_audio_frames
+        self._compaction_audio_frames = []
+        self._compaction_audio_bytes = 0
+        logger.debug(
+            f"{self}: replaying {len(frames)} user audio frame(s) held during compaction"
+        )
+        for frame in frames:
+            await super()._send_user_audio(frame)
+
+    def _discard_compaction_audio(self) -> None:
+        self._compaction_audio_frames = []
+        self._compaction_audio_bytes = 0
+
     # ------------------------------------------------------------------
     # Context lifecycle: Dograh pre-populates self._context via the engine,
     # so upstream's "first arrival === self._context is None" check doesn't
@@ -359,6 +525,16 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
             self._ready_for_realtime_input = False
             await self._maybe_seed_node_transition_context()
             return
+        if self._awaiting_context_compaction_seed:
+            # Compaction refresh: the context is already complete, so seed it
+            # straight away. for_reconnect keeps the seed silent on Gemini 3.x,
+            # which is the only place compaction is enabled.
+            self._awaiting_context_compaction_seed = False
+            self._ready_for_realtime_input = True
+            await self._create_initial_response(for_reconnect=True)
+            await self._flush_compaction_audio()
+            await self._drain_pending_tool_results()
+            return
         self._ready_for_realtime_input = True
         if self._run_llm_when_session_ready:
             # Context arrived before session was ready — fulfil the queued
```

### G.2 Reconnect race fix (`a66dd0ba`)

```diff
diff --git a/api/services/pipecat/realtime/gemini_live.py b/api/services/pipecat/realtime/gemini_live.py
index 476f010d..fcead39c 100644
--- a/api/services/pipecat/realtime/gemini_live.py
+++ b/api/services/pipecat/realtime/gemini_live.py
@@ -19,6 +19,7 @@ Layers Dograh engine integration quirks onto upstream-pristine
 """
 
 import asyncio
+import contextvars
 import os
 from typing import Any
 
@@ -68,6 +69,14 @@ COMPRESSION_TRIGGER_TOKENS = int(
     os.getenv("GEMINI_LIVE_COMPRESSION_TRIGGER_TOKENS", "16000")
 )
 
+# The connection epoch a connection's background loop was started under. Each
+# connection runs in its own asyncio task, and a task gets its own copy of a
+# context variable, so every loop can tell whether it has been superseded
+# without changing upstream's loop. See _handle_connection_error.
+_LOOP_CONNECTION_EPOCH: contextvars.ContextVar[int | None] = contextvars.ContextVar(
+    "gemini_live_loop_connection_epoch", default=None
+)
+
 
 class DograhGeminiLiveLLMService(GeminiLiveLLMService):
     """Gemini Live with Dograh engine integration quirks. See module docstring."""
@@ -116,6 +125,9 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
         # here and replay it once the fresh session is seeded.
         self._compaction_audio_frames: list[Any] = []
         self._compaction_audio_bytes: int = 0
+        # Advanced by every _disconnect(); a connection loop started under an
+        # older value has been superseded. See _handle_connection_error.
+        self._connection_epoch: int = 0
 
     # ------------------------------------------------------------------
     # Hooks from upstream GeminiLiveLLMService
@@ -267,9 +279,81 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
         self._node_transition_context_received = False
         self._node_transition_context_seed_started = False
         self._session_resumption_handle = None
+        outgoing_task = self._connection_task
         await self._disconnect()
+        await self._await_outgoing_connection_stopped(outgoing_task)
         await self._connect(session_resumption_handle=None)
 
+    # ------------------------------------------------------------------
+    # Superseded connections must not trigger reconnects (run-442)
+    # ------------------------------------------------------------------
+    #
+    # Upstream's _disconnect() cancels the outgoing connection's loop with a
+    # best-effort ~1s timeout and moves on whether or not it stopped. A loop
+    # that survives then fails on its next read — self._session is already
+    # cleared or belongs to the newer connection ("'NoneType' object has no
+    # attribute 'receive'") — and upstream treats that as a real failure: it
+    # counts it, reconnects on top of the newer connection, and after three
+    # rounds ends the call with "Gemini Live connection failed after 3
+    # consecutive attempts". These three overrides stop that without touching
+    # upstream code: every error upstream reacts to goes through
+    # _handle_connection_error, which now ignores errors from a loop that a
+    # newer connection has replaced.
+
+    async def _disconnect(self, *args, **kwargs):
+        # First, before any await inside upstream's teardown can hand control
+        # back to the outgoing loop. Arguments pass through: newer pipecat
+        # takes _disconnect(preserve_pending_end_frame=...).
+        self._connection_epoch += 1
+        return await super()._disconnect(*args, **kwargs)
+
+    async def _connection_task_handler(self, config):
+        # Runs inside the connection's own task, so this binding is private to
+        # this connection's loop.
+        _LOOP_CONNECTION_EPOCH.set(self._connection_epoch)
+        return await super()._connection_task_handler(config=config)
+
+    async def _handle_connection_error(self, error: Exception) -> bool:
+        started_under = _LOOP_CONNECTION_EPOCH.get()
+        if started_under is not None and started_under != self._connection_epoch:
+            logger.info(
+                f"{self}: ignoring error from a superseded Gemini Live connection: {error}"
+            )
+            return False  # upstream then lets the old loop exit; no retry, no count
+        return await super()._handle_connection_error(error)
+
+    async def _await_outgoing_connection_stopped(
+        self, task: asyncio.Task | None, *, timeout: float = 3.0
+    ) -> None:
+        """Make sure a connection being replaced has actually stopped before we
+        start the next one.
+
+        ``_disconnect()`` (upstream) cancels the outgoing connection's background
+        loop with only a best-effort ~1s timeout, and proceeds regardless of
+        whether that succeeded. If the loop is still alive when a new connection
+        starts, it can still be mid-iteration reading ``self._session`` right as
+        this method reassigns it — the connection-epoch guard (see
+        ``_handle_connection_error``) makes that harmless if it still happens, but
+        giving the outgoing loop a real chance to finish first means it usually
+        doesn't happen at all, avoiding the wasted reconnect-and-discard cycle.
+
+        Bounded rather than unconditional: a connection that never stops must
+        not block reconnection forever, which would turn a freeze into a
+        permanent one.
+        """
+        if task is None or task.done():
+            return
+        try:
+            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
+        except (TimeoutError, asyncio.CancelledError):
+            logger.warning(
+                f"{self}: outgoing Gemini Live connection did not stop in time"
+            )
+        except Exception:
+            # Whatever it failed with is the outgoing connection's own concern —
+            # already routed through its own error handling before we got here.
+            pass
+
     # ------------------------------------------------------------------
     # Cost compaction: trade a reconnect for a cheaper context
     # ------------------------------------------------------------------
@@ -347,7 +431,9 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
         logger.info(f"{self}: compacting Gemini Live context via session refresh")
         self._awaiting_context_compaction_seed = True
         self._session_resumption_handle = None
+        outgoing_task = self._connection_task
         await self._disconnect()
+        await self._await_outgoing_connection_stopped(outgoing_task)
         await self._connect(session_resumption_handle=None)
 
     # ------------------------------------------------------------------
```

### G.3 Node-transition guard (`47a96244`)

```diff
diff --git a/api/services/pipecat/realtime/gemini_live.py b/api/services/pipecat/realtime/gemini_live.py
index fcead39c..4aa2619a 100644
--- a/api/services/pipecat/realtime/gemini_live.py
+++ b/api/services/pipecat/realtime/gemini_live.py
@@ -644,6 +644,15 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
             not self._awaiting_node_transition_context
             or not self._node_transition_context_received
             or not self._session
+            # A node-transition context frame can arrive while the reconnect's
+            # disconnect is still in flight: _session still points to the old
+            # session being torn down, so `not self._session` above does not yet
+            # protect us. Seeding here would run against the dying session and
+            # clear the node-transition flags, so the real seed never happens
+            # when the fresh session is ready, and every word the caller says
+            # after the transition is dropped. Wait until the reconnect settles.
+            # (Upstream dograh fa1df112; seen here on run 600.)
+            or self._disconnecting
             or self._node_transition_context_seed_started
         ):
             return
```

### G.4 Recorded greeting (`c535bd4a`)

```diff
diff --git a/api/services/pipecat/audio_playback.py b/api/services/pipecat/audio_playback.py
index ab0e5041..69e36f19 100644
--- a/api/services/pipecat/audio_playback.py
+++ b/api/services/pipecat/audio_playback.py
@@ -15,6 +15,7 @@ from loguru import logger
 
 from pipecat.frames.frames import (
     Frame,
+    LLMAssistantPushAggregationFrame,
     OutputAudioRawFrame,
     TTSAudioRawFrame,
     TTSStartedFrame,
@@ -137,6 +138,12 @@ async def play_audio(
         )
     )
     await queue_frame(TTSStoppedFrame(context_id=context_id))
+    if transcript and append_to_context:
+        # Nothing else closes an assistant turn opened by recorded audio: only a
+        # TTS service emits this frame, and the recording bypasses TTS. Without
+        # it the transcript sits in the aggregator until the next LLM response
+        # ends, and is committed as part of *that* message. (Upstream 9c82ceb0.)
+        await queue_frame(LLMAssistantPushAggregationFrame())
 
 
 async def play_audio_loop(
diff --git a/api/services/pipecat/realtime/gemini_live.py b/api/services/pipecat/realtime/gemini_live.py
index 4aa2619a..5d6f2f26 100644
--- a/api/services/pipecat/realtime/gemini_live.py
+++ b/api/services/pipecat/realtime/gemini_live.py
@@ -107,6 +107,9 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
         # Text greeting captured from the first TTSSpeakFrame while the Gemini
         # session is still connecting.
         self._pending_initial_greeting_text: str | None = None
+        # Greeting a recording already delivered, held until a session exists.
+        # A 1-tuple, because the transcript itself may legitimately be None.
+        self._pending_prerecorded_greeting: tuple[str | None] | None = None
         self._transition_function_call_task: asyncio.Task | None = None
         # Intentional node changes use a fresh, context-seeded connection rather
         # than a potentially stale session-resumption handle. The new connection
@@ -549,6 +552,84 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
             self._context = context
             await self._process_completed_function_calls(send_new_results=True)
 
+    async def handle_prerecorded_greeting(
+        self, context: LLMContext | None, transcript: str | None
+    ) -> None:
+        """Open the conversation after a recording has greeted the caller.
+
+        A recorded greeting is played straight to the output transport, so the
+        opening TTSSpeakFrame never reaches this service: without this, Gemini
+        is never told the call started or what it already said, and its first
+        reply greets the caller a second time. Seed the greeting the caller
+        heard as a model turn, accept caller audio, and generate nothing.
+
+        ``transcript`` arrives here rather than through LLMContext because the
+        recording is still playing; the aggregator commits it only once
+        playback drains, after this seed is sent. (Ported from upstream dograh
+        9c82ceb0, Gemini part.)
+        """
+        if self._handled_initial_context:
+            return
+        if context is None:
+            logger.warning(
+                f"{self}: received prerecorded greeting before context was set"
+            )
+            return
+        self._handled_initial_context = True
+        self._context = context
+        # A fresh session never issued the tool calls in this history, so mark
+        # their results delivered rather than sending them as tool responses.
+        await self._process_completed_function_calls(send_new_results=False)
+        self._pending_tool_results.clear()
+        await self._create_prerecorded_greeting_response(transcript)
+
+    async def _create_prerecorded_greeting_response(self, transcript: str | None):
+        """Seed the spoken greeting, leaving the turn open for the caller.
+
+        ``turn_complete=False`` is what separates this from every other
+        opening: Gemini takes the history but is not asked to produce a turn,
+        so it waits for the caller instead of greeting them again.
+        """
+        if self._disconnecting:
+            return
+
+        if not self._session:
+            self._pending_prerecorded_greeting = (transcript,)
+            self._run_llm_when_session_ready = True
+            return
+
+        self._pending_prerecorded_greeting = None
+
+        adapter = self.get_llm_adapter()
+        turns = list(
+            adapter.get_llm_invocation_params(self._context).get("messages", [])
+        )
+        if transcript:
+            turns.append(Content(role="model", parts=[Part(text=transcript)]))
+        if not self._is_gemini_3 and (
+            not turns or getattr(turns[-1], "role", None) != "user"
+        ):
+            # Gemini 2.5 requires a seed to end on a user turn; padding one that
+            # already does would put two user turns back to back.
+            turns.append(Content(role="user", parts=[Part(text=" ")]))
+
+        logger.debug("Seeding Gemini Live with a prerecorded greeting")
+
+        try:
+            if turns:
+                await self._session.send_client_content(
+                    turns=turns, turn_complete=False
+                )
+        except Exception as e:
+            await self._handle_send_error(e)
+
+        if not self._is_gemini_3:
+            # 2.5 only picks seeded history up once a turn completes; let the
+            # caller's first utterance carry that completion.
+            self._needs_initial_turn_complete_message = True
+
+        self._ready_for_realtime_input = True
+
     async def _handle_initial_greeting(self, context: LLMContext, greeting_text: str):
         """Trigger the first Gemini turn with an exact static text greeting."""
         if context is None:
@@ -626,7 +707,10 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
             # Context arrived before session was ready — fulfil the queued
             # initial response now.
             self._run_llm_when_session_ready = False
-            if self._pending_initial_greeting_text is not None:
+            if self._pending_prerecorded_greeting is not None:
+                (transcript,) = self._pending_prerecorded_greeting
+                await self._create_prerecorded_greeting_response(transcript)
+            elif self._pending_initial_greeting_text is not None:
                 await self._create_initial_greeting_response(
                     self._pending_initial_greeting_text
                 )
diff --git a/api/services/workflow/pipecat_engine.py b/api/services/workflow/pipecat_engine.py
index 86540d55..273f1dc1 100644
--- a/api/services/workflow/pipecat_engine.py
+++ b/api/services/workflow/pipecat_engine.py
@@ -685,6 +685,9 @@ class PipecatEngine:
                             transcript=result.transcript,
                             append_to_context=True,
                         )
+                        await self._open_realtime_after_recorded_greeting(
+                            result.transcript
+                        )
                         return "greeting"
                     logger.warning(
                         f"Failed to fetch audio greeting {greeting_value}, "
@@ -713,6 +716,26 @@ class PipecatEngine:
 
         return "none"
 
+    async def _open_realtime_after_recorded_greeting(
+        self, transcript: str | None
+    ) -> None:
+        """Hand the opening turn to a realtime service after a recording.
+
+        A recorded greeting is queued straight to the output transport, so a
+        realtime service (Gemini Live) is otherwise never told the call has
+        started or what was said, and greets the caller again on its first
+        reply. The transcript goes with it because the recording is still
+        playing: the assistant aggregator only commits it to LLMContext once
+        playback drains. A no-op for text LLMs, which re-read the context on
+        every generation. (Upstream dograh 9c82ceb0.)
+        """
+        handle_prerecorded_greeting = getattr(
+            self.llm, "handle_prerecorded_greeting", None
+        )
+        if handle_prerecorded_greeting is None:
+            return
+        await handle_prerecorded_greeting(self.context, transcript)
+
     async def _handle_end_node(self, node: Node) -> None:
         """Handle end node execution."""
         # Setup LLM context with prompts and functions.
```
## Appendix H: the complete 3.8 provider diff (`47a96244`, every file)

This is the same change as section 5, as one diff.

```diff
diff --git a/api/services/configuration/check_validity.py b/api/services/configuration/check_validity.py
index bc1400de..a81f44fe 100644
--- a/api/services/configuration/check_validity.py
+++ b/api/services/configuration/check_validity.py
@@ -57,6 +57,7 @@ class UserConfigurationValidator:
             ServiceProviders.GROK_REALTIME.value: self._check_grok_realtime_api_key,
             ServiceProviders.ULTRAVOX_REALTIME.value: self._check_ultravox_realtime_api_key,
             ServiceProviders.GOOGLE_REALTIME.value: self._check_google_api_key,
+            ServiceProviders.GEMINI_LIVE_38.value: self._check_google_api_key,
             ServiceProviders.GOOGLE_VERTEX_REALTIME.value: self._check_google_vertex_realtime_api_key,
             ServiceProviders.AZURE_REALTIME.value: self._check_azure_realtime_api_key,
             ServiceProviders.ASSEMBLYAI.value: self._check_assemblyai_api_key,
diff --git a/api/services/configuration/options/__init__.py b/api/services/configuration/options/__init__.py
index 93ab358e..f064ca42 100644
--- a/api/services/configuration/options/__init__.py
+++ b/api/services/configuration/options/__init__.py
@@ -28,6 +28,7 @@ from .google import (
     GOOGLE_MODELS,
     GOOGLE_REALTIME_LANGUAGES,
     GOOGLE_REALTIME_MODELS,
+    GEMINI_LIVE_38_MODELS,
     GOOGLE_REALTIME_VOICES,
     GOOGLE_STT_LANGUAGES,
     GOOGLE_STT_MODELS,
@@ -82,6 +83,7 @@ __all__ = [
     "GOOGLE_MODELS",
     "GOOGLE_REALTIME_LANGUAGES",
     "GOOGLE_REALTIME_MODELS",
+    "GEMINI_LIVE_38_MODELS",
     "GOOGLE_REALTIME_VOICES",
     "GOOGLE_STT_LANGUAGES",
     "GOOGLE_STT_MODELS",
diff --git a/api/services/configuration/options/google.py b/api/services/configuration/options/google.py
index a16bbe07..dfaea230 100644
--- a/api/services/configuration/options/google.py
+++ b/api/services/configuration/options/google.py
@@ -12,6 +12,10 @@ GOOGLE_VERTEX_MODELS = (
 )
 
 GOOGLE_REALTIME_MODELS = ("gemini-3.1-flash-live-preview",)
+# Gemini 3.8 Live has its own provider (see GeminiLive38LLMConfiguration):
+# its tool semantics differ from 3.1's. Plain 3.8 only; the extended-thinking
+# variant needs pipecat v1.12.
+GEMINI_LIVE_38_MODELS = ("gemini-3.8-live",)
 GOOGLE_REALTIME_VOICES = ("Puck", "Charon", "Kore", "Fenrir", "Aoede")
 GOOGLE_REALTIME_LANGUAGES = (
     "ar",
diff --git a/api/services/configuration/registry.py b/api/services/configuration/registry.py
index 5e21eea6..d451ce49 100644
--- a/api/services/configuration/registry.py
+++ b/api/services/configuration/registry.py
@@ -29,6 +29,7 @@ from api.services.configuration.options import (
     GOOGLE_MODELS,
     GOOGLE_REALTIME_LANGUAGES,
     GOOGLE_REALTIME_MODELS,
+    GEMINI_LIVE_38_MODELS,
     GOOGLE_REALTIME_VOICES,
     GOOGLE_STT_LANGUAGES,
     GOOGLE_STT_MODELS,
@@ -93,6 +94,7 @@ class ServiceProviders(str, Enum):
     GROK_REALTIME = "grok_realtime"
     ULTRAVOX_REALTIME = "ultravox_realtime"
     GOOGLE_REALTIME = "google_realtime"
+    GEMINI_LIVE_38 = "gemini_live_38"
     GOOGLE_VERTEX_REALTIME = "google_vertex_realtime"
     AZURE_REALTIME = "azure_realtime"
     SMALLEST = "smallest"
@@ -125,6 +127,7 @@ class BaseServiceConfiguration(BaseModel):
         ServiceProviders.GROK_REALTIME,
         ServiceProviders.ULTRAVOX_REALTIME,
         ServiceProviders.GOOGLE_REALTIME,
+        ServiceProviders.GEMINI_LIVE_38,
         ServiceProviders.GOOGLE_VERTEX_REALTIME,
         ServiceProviders.AZURE_REALTIME,
         ServiceProviders.SARVAM,
@@ -258,6 +261,14 @@ OPENAI_REALTIME_PROVIDER_MODEL_CONFIG = provider_model_config("OpenAI Realtime")
 GROK_REALTIME_PROVIDER_MODEL_CONFIG = provider_model_config("Grok Realtime")
 ULTRAVOX_REALTIME_PROVIDER_MODEL_CONFIG = provider_model_config("Ultravox Realtime")
 GOOGLE_REALTIME_PROVIDER_MODEL_CONFIG = provider_model_config("Google Realtime")
+GEMINI_LIVE_38_PROVIDER_MODEL_CONFIG = provider_model_config(
+    "Gemini 3.8 Live",
+    description=(
+        "Gemini 3.8 Live on Google AI Studio, with 3.8's tool behaviour: tools "
+        "are declared blocking so the agent waits for their results. Uses the "
+        "same Google API key as Google Realtime."
+    ),
+)
 GOOGLE_VERTEX_REALTIME_PROVIDER_MODEL_CONFIG = provider_model_config(
     "Google Vertex Realtime"
 )
@@ -738,6 +749,36 @@ class GoogleRealtimeLLMConfiguration(BaseLLMConfiguration):
     )
 
 
+@register_service(ServiceType.REALTIME)
+class GeminiLive38LLMConfiguration(BaseLLMConfiguration):
+    model_config = GEMINI_LIVE_38_PROVIDER_MODEL_CONFIG
+    provider: Literal[ServiceProviders.GEMINI_LIVE_38] = ServiceProviders.GEMINI_LIVE_38
+    model: str = Field(
+        default="gemini-3.8-live",
+        description="Gemini 3.8 Live model on Google AI Studio (not Vertex).",
+        json_schema_extra={
+            "examples": GEMINI_LIVE_38_MODELS,
+            "allow_custom_input": True,
+        },
+    )
+    voice: str = Field(
+        default="Puck",
+        description="Voice the model speaks in.",
+        json_schema_extra={
+            "examples": GOOGLE_REALTIME_VOICES,
+            "allow_custom_input": True,
+        },
+    )
+    language: str = Field(
+        default="en",
+        description="ISO 639-1 language code.",
+        json_schema_extra={
+            "examples": GOOGLE_REALTIME_LANGUAGES,
+            "allow_custom_input": True,
+        },
+    )
+
+
 @register_service(ServiceType.REALTIME)
 class GoogleVertexRealtimeLLMConfiguration(BaseLLMConfiguration):
     model_config = GOOGLE_VERTEX_REALTIME_PROVIDER_MODEL_CONFIG
@@ -830,6 +871,7 @@ REALTIME_PROVIDERS = {
     ServiceProviders.GROK_REALTIME.value,
     ServiceProviders.ULTRAVOX_REALTIME.value,
     ServiceProviders.GOOGLE_REALTIME.value,
+    ServiceProviders.GEMINI_LIVE_38.value,
     ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
     ServiceProviders.AZURE_REALTIME.value,
 }
@@ -859,6 +901,7 @@ RealtimeConfig = Annotated[
         GrokRealtimeLLMConfiguration,
         UltravoxRealtimeLLMConfiguration,
         GoogleRealtimeLLMConfiguration,
+        GeminiLive38LLMConfiguration,
         GoogleVertexRealtimeLLMConfiguration,
         AzureRealtimeLLMConfiguration,
     ],
diff --git a/api/services/integrations/tuner/runtime.py b/api/services/integrations/tuner/runtime.py
index d537597f..83eb5d92 100644
--- a/api/services/integrations/tuner/runtime.py
+++ b/api/services/integrations/tuner/runtime.py
@@ -28,6 +28,7 @@ def _resolve_model_labels(context: IntegrationRuntimeContext) -> tuple[str, str,
         llm_model = _format_model_label(realtime_provider, realtime_model)
         if realtime_provider in {
             ServiceProviders.GOOGLE_REALTIME.value,
+            ServiceProviders.GEMINI_LIVE_38.value,
             ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
             ServiceProviders.OPENAI_REALTIME.value,
         }:
diff --git a/api/services/pipecat/realtime/gemini_live.py b/api/services/pipecat/realtime/gemini_live.py
index fcead39c..4aa2619a 100644
--- a/api/services/pipecat/realtime/gemini_live.py
+++ b/api/services/pipecat/realtime/gemini_live.py
@@ -644,6 +644,15 @@ class DograhGeminiLiveLLMService(GeminiLiveLLMService):
             not self._awaiting_node_transition_context
             or not self._node_transition_context_received
             or not self._session
+            # A node-transition context frame can arrive while the reconnect's
+            # disconnect is still in flight: _session still points to the old
+            # session being torn down, so `not self._session` above does not yet
+            # protect us. Seeding here would run against the dying session and
+            # clear the node-transition flags, so the real seed never happens
+            # when the fresh session is ready, and every word the caller says
+            # after the transition is dropped. Wait until the reconnect settles.
+            # (Upstream dograh fa1df112; seen here on run 600.)
+            or self._disconnecting
             or self._node_transition_context_seed_started
         ):
             return
diff --git a/api/services/pipecat/realtime/gemini_live_38.py b/api/services/pipecat/realtime/gemini_live_38.py
new file mode 100644
index 00000000..20ea903c
--- /dev/null
+++ b/api/services/pipecat/realtime/gemini_live_38.py
@@ -0,0 +1,115 @@
+"""Gemini 3.8 Live, kept apart from the Gemini 3.1 path.
+
+The 3.8 Live family changed how it runs tools, and the pipecat we ship
+(dograh-hq/pipecat at the v1.5 merge) predates that. Rather than upgrade
+pipecat for the whole app, this service carries the 3.8-specific behaviour,
+ported from pipecat v1.12 (``GeminiLiveLLMService._tag_tool_behaviors`` and
+``_tool_result``), and is selected only through its own provider
+(``ServiceProviders.GEMINI_LIVE_38``). The Gemini 3.1 provider and
+``DograhGeminiLiveLLMService`` are untouched.
+
+What differs on 3.8:
+
+- **Tools default to NON_BLOCKING.** Without an explicit ``BLOCKING``
+  declaration, 3.8 keeps talking while a tool runs and answers before the
+  result lands — on a Vectus call it invented the Area Manager instead of
+  waiting for ``vectus_area_manager_lookup``. Synchronous tools are declared
+  ``BLOCKING``; tools registered with ``cancel_on_interruption=False`` stay
+  ``NON_BLOCKING`` (with the ``WHEN_IDLE`` scheduling hint on their results).
+- **No nudge after a tool result.** Older 3.x needed a blank realtime-input
+  nudge to continue after a tool response; 3.8 continues on its own, and the
+  extra input can read as a new user turn.
+
+Only plain ``gemini-3.8-live`` is supported here. The extended-thinking
+variant runs every tool NON_BLOCKING and reports ``interaction_status``, which
+needs pipecat v1.12's turn handling; use it only after that upgrade.
+"""
+
+from __future__ import annotations
+
+from typing import Any
+
+from google.genai.types import FunctionResponse
+from loguru import logger
+
+from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService
+
+DEFAULT_MODEL = "gemini-3.8-live"
+
+
+def tag_tool_behaviors(tools: Any, is_async) -> int:
+    """Declare each function ``BLOCKING`` or ``NON_BLOCKING`` for 3.8.
+
+    ``is_async(name)`` says whether the tool was registered with
+    ``cancel_on_interruption=False``. Returns how many declarations were
+    tagged. Mutates ``tools`` (Gemini's dict form) in place.
+    """
+    tagged = 0
+    if not isinstance(tools, list):
+        return tagged
+    for tool in tools:
+        if not isinstance(tool, dict):
+            continue
+        decls = tool.get("function_declarations")
+        if not isinstance(decls, list):
+            continue
+        for decl in decls:
+            if not isinstance(decl, dict):
+                continue
+            name = decl.get("name")
+            if not isinstance(name, str):
+                continue
+            decl["behavior"] = "NON_BLOCKING" if is_async(name) else "BLOCKING"
+            tagged += 1
+    return tagged
+
+
+class DograhGemini38LiveLLMService(DograhGeminiLiveLLMService):
+    """``DograhGeminiLiveLLMService`` with Gemini 3.8 Live tool semantics."""
+
+    @property
+    def _supports_non_blocking_tools(self) -> bool:
+        # 3.8 supports NON_BLOCKING declarations and the scheduling hint on
+        # results (older 3.x did not, which is what the parent assumes for any
+        # gemini-3 model).
+        return True
+
+    async def _connection_task_handler(self, config):
+        # The finished LiveConnectConfig, just before the session opens: make
+        # every synchronous tool BLOCKING, since 3.8 would otherwise run it in
+        # the background and answer without the result.
+        tagged = tag_tool_behaviors(getattr(config, "tools", None), self._function_is_async)
+        if tagged:
+            logger.debug(f"{self}: declared behavior on {tagged} Gemini 3.8 tool(s)")
+        return await super()._connection_task_handler(config)
+
+    async def _tool_result(
+        self, tool_call_id: str, tool_name: str, tool_result_message: dict[str, Any]
+    ) -> bool:
+        """Send a tool result, without the older-3.x continuation nudge.
+
+        Same as pipecat's ``_tool_result`` at the pinned version, minus the
+        trailing ``send_realtime_input(text=" ")``, matching pipecat v1.12.
+        """
+        if self._disconnecting or not self._session:
+            logger.debug(
+                f"{self}: queueing tool result for tool_call_id={tool_call_id} until session is ready"
+            )
+            self._pending_tool_results[tool_call_id] = (tool_name, tool_result_message)
+            return False
+
+        if self._function_is_async(tool_name):
+            response_payload = {**tool_result_message, "scheduling": "WHEN_IDLE"}
+        else:
+            response_payload = tool_result_message
+
+        response = FunctionResponse(name=tool_name, id=tool_call_id, response=response_payload)
+        try:
+            await self._session.send_tool_response(function_responses=response)
+        except Exception as e:
+            self._pending_tool_results[tool_call_id] = (tool_name, tool_result_message)
+            await self._handle_send_error(e)
+            return False
+
+        self._pending_tool_results.pop(tool_call_id, None)
+        return True
diff --git a/api/services/pipecat/run_pipeline.py b/api/services/pipecat/run_pipeline.py
index 75719a53..f3790259 100644
--- a/api/services/pipecat/run_pipeline.py
+++ b/api/services/pipecat/run_pipeline.py
@@ -227,6 +227,7 @@ def _create_realtime_user_turn_config(provider: str):
 
     if provider in {
         ServiceProviders.GOOGLE_REALTIME.value,
+        ServiceProviders.GEMINI_LIVE_38.value,
         ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
     }:
         # Let Gemini Live own barge-in via its server-side VAD, but keep local
diff --git a/api/services/pipecat/service_factory.py b/api/services/pipecat/service_factory.py
index 50ef9dfb..481b2094 100644
--- a/api/services/pipecat/service_factory.py
+++ b/api/services/pipecat/service_factory.py
@@ -1148,6 +1148,32 @@ def create_realtime_llm_service(user_config, audio_config: "AudioConfig"):
             api_key=api_key,
             settings=DograhGeminiLiveLLMService.Settings(**settings_kwargs),
         )
+    elif provider == ServiceProviders.GEMINI_LIVE_38.value:
+        from api.services.pipecat.realtime.gemini_live import (
+            COMPRESSION_TRIGGER_TOKENS,
+        )
+        from api.services.pipecat.realtime.gemini_live_38 import (
+            DEFAULT_MODEL,
+            DograhGemini38LiveLLMService,
+        )
+        from pipecat.services.google.gemini_live.llm import (
+            ContextWindowCompressionParams,
+        )
+
+        settings_kwargs = {
+            "model": model or DEFAULT_MODEL,
+            "voice": voice or "Puck",
+        }
+        if COMPRESSION_TRIGGER_TOKENS > 0:
+            settings_kwargs["context_window_compression"] = ContextWindowCompressionParams(
+                enabled=True, trigger_tokens=COMPRESSION_TRIGGER_TOKENS
+            )
+        if language:
+            settings_kwargs["language"] = language
+        return DograhGemini38LiveLLMService(
+            api_key=api_key,
+            settings=DograhGemini38LiveLLMService.Settings(**settings_kwargs),
+        )
     elif provider == ServiceProviders.GOOGLE_VERTEX_REALTIME.value:
         from api.services.pipecat.realtime.gemini_live import (
             COMPRESSION_TRIGGER_TOKENS,
```
