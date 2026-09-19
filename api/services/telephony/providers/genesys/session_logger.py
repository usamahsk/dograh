"""Live Genesys conversation-attribute logging for AudioHook sessions.

Accumulates the call's turns (user/agent messages, tool executions, node
decisions) into the client-agreed JSON shape and PATCHes it onto the Genesys
conversation's customer participant as attributes — after every turn, so data
survives immature call termination.

Payload (one attribute holding the JSON string, plus flat metadata attrs):

{
  "GenesysConversationID": "...",
  "AIAgentSessionID": "dograh-run-123",
  "CallStartTime": "2026-09-02T10:25:00Z",
  "CallEndTime": null,
  "CallStatus": "InProgress" | "Completed",
  "ANI": "tel:+1...", "DNIS": "tel:+1...",
  "Language": "en-US",
  "turn": [ {"speaker": "AIAgent"|"user"|"Tool"|"AI_Decision", ...} ]
}
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from pipecat.utils.enums import RealtimeFeedbackType

from .genesys_api import GenesysApiClient

_PUSH_DEBOUNCE_SECONDS = 2.0


class GenesysSessionLogger:
    """Collects per-turn activity and mirrors it to Genesys conversation attributes."""

    def __init__(
        self,
        *,
        api_client: Optional[GenesysApiClient],
        workflow_run_id: int,
        ani: Optional[str] = None,
        dnis: Optional[str] = None,
        language: Optional[str] = None,
    ):
        self._api_client = api_client
        self._workflow_run_id = workflow_run_id
        self._ani = ani or ""
        self._dnis = dnis or ""
        self._language = language or ""
        self._conversation_id: Optional[str] = None
        self._participant_id: Optional[str] = None
        self._session_id: Optional[str] = None
        self._call_start_time = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._call_end_time: Optional[str] = None
        self._status = "InProgress"
        self._turns: List[Dict[str, Any]] = []
        self._pending_function_calls: Dict[str, Dict[str, Any]] = {}
        self._push_task: Optional[asyncio.Task] = None
        self._dirty = False
        self._disabled = api_client is None
        self._warned = False

    # ---- wiring -----------------------------------------------------------

    def set_conversation_id(self, conversation_id: Optional[str]) -> None:
        self._conversation_id = conversation_id

    def set_session_id(self, session_id: Optional[str]) -> None:
        self._session_id = session_id

    async def handle_event(self, event: dict) -> None:
        """Listener for InMemoryLogsBuffer feedback events (called as a task)."""
        if self._disabled:
            return
        event_type = event.get("type")
        payload = event.get("payload") or {}

        if event_type == RealtimeFeedbackType.USER_TRANSCRIPTION.value:
            if payload.get("final") and payload.get("text"):
                self._add_turn({"speaker": "user", "message": payload["text"]})
        elif event_type == RealtimeFeedbackType.BOT_TEXT.value:
            if payload.get("text"):
                self._add_turn({"speaker": "AIAgent", "message": payload["text"]})
        elif event_type == RealtimeFeedbackType.FUNCTION_CALL_START.value:
            tool_call_id = payload.get("tool_call_id") or ""
            self._pending_function_calls[tool_call_id] = {
                "name": payload.get("function_name") or "",
                "request": payload.get("arguments") or {},
            }
        elif event_type == RealtimeFeedbackType.FUNCTION_CALL_END.value:
            tool_call_id = payload.get("tool_call_id") or ""
            started = self._pending_function_calls.pop(tool_call_id, None)
            name = payload.get("function_name") or (started or {}).get("name") or ""
            turn = {
                "speaker": "Tool",
                "name": name,
                "status": "success" if payload.get("result") is not None else "unknown",
            }
            if started and started.get("request"):
                turn["request"] = started["request"]
            if payload.get("result") is not None:
                turn["response"] = payload["result"]
            self._add_turn(turn)
        elif event_type == RealtimeFeedbackType.NODE_TRANSITION.value:
            node_name = payload.get("node_name")
            if node_name:
                self._add_turn({"speaker": "AI_Decision", "action": node_name})
        else:
            return

        self._schedule_push()

    # ---- turn accumulation ------------------------------------------------

    def _add_turn(self, turn: Dict[str, Any]) -> None:
        self._turns.append(turn)
        logger.debug(
            f"[run {self._workflow_run_id}] Genesys session log turn #{len(self._turns)}: "
            f"{turn.get('speaker')}"
        )

    def _schedule_push(self) -> None:
        self._dirty = True
        if self._push_task is None or self._push_task.done():
            self._push_task = asyncio.create_task(self._debounced_push())

    async def _debounced_push(self) -> None:
        try:
            await asyncio.sleep(_PUSH_DEBOUNCE_SECONDS)
            # Keep pushing while turns keep arriving during the debounce window.
            while self._dirty:
                self._dirty = False
                await self.push()
                if self._dirty:
                    await asyncio.sleep(_PUSH_DEBOUNCE_SECONDS)
        except Exception as e:
            logger.warning(f"[run {self._workflow_run_id}] Genesys session push failed: {e}")

    # ---- payload + push ---------------------------------------------------

    def _payload(self) -> Dict[str, Any]:
        return {
            "GenesysConversationID": self._conversation_id or "",
            "AIAgentSessionID": self._session_id or f"dograh-run-{self._workflow_run_id}",
            "CallStartTime": self._call_start_time,
            "CallEndTime": self._call_end_time,
            "CallStatus": self._status,
            "ANI": self._ani,
            "DNIS": self._dnis,
            "Language": self._language,
            "turn": self._turns,
        }

    async def push(self) -> None:
        """Push the current snapshot onto the conversation attributes."""
        if self._disabled or not self._conversation_id:
            if not self._warned and self._disabled:
                logger.info(
                    f"[run {self._workflow_run_id}] Genesys live logging disabled "
                    f"(no API credentials on the telephony configuration)"
                )
                self._warned = True
            return
        if not self._participant_id:
            self._participant_id = await self._api_client.find_customer_participant_id(
                self._conversation_id
            )
            if not self._participant_id:
                return

        payload = self._payload()
        attributes = {
            "aiSessionData": json.dumps(payload, default=str),
            "CallStatus": self._status,
        }
        if self._ani:
            attributes["ANI"] = self._ani
        if self._dnis:
            attributes["DNIS"] = self._dnis

        ok = await self._api_client.update_participant_attributes(
            self._conversation_id, self._participant_id, attributes
        )
        if ok:
            logger.info(
                f"[run {self._workflow_run_id}] Genesys attributes updated "
                f"({len(self._turns)} turns, status={self._status})"
            )

    async def finalize(self) -> None:
        """Mark the call completed and push the final snapshot."""
        if self._disabled:
            return
        self._status = "Completed"
        self._call_end_time = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            if self._push_task and not self._push_task.done():
                await asyncio.wait_for(self._push_task, timeout=5)
        except Exception:
            pass
        try:
            await self.push()
        except Exception as e:
            logger.warning(f"[run {self._workflow_run_id}] Genesys finalize push failed: {e}")
