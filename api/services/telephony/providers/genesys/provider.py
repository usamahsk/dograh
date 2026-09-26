"""Genesys Cloud CX AudioHook implementation of the TelephonyProvider interface.

Genesys Cloud dials *out* to Dograh: the Architect "Call Audio Connector"
action opens a WebSocket to the endpoint exposed by this package's routes
(``/api/v1/telephony/genesys/{connector_id}``) and streams AudioHook protocol
messages. There are no HTTP webhooks and Dograh cannot originate calls on
Genesys; sessions always begin with an inbound AudioHook connection.
"""

import asyncio
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi import Response
from loguru import logger

from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    TelephonyProvider,
)

from .security import verify_audiohook_signature

if TYPE_CHECKING:
    from fastapi import WebSocket

# Genesys sends the AudioHook ``open`` message immediately after the media
# stream opens. The agent-stream route holds an org concurrency slot while we
# wait, so an idle socket must not be able to hold it indefinitely.
AGENT_STREAM_HANDSHAKE_TIMEOUT_S = 10


class GenesysProvider(TelephonyProvider):
    """Genesys Cloud CX AudioHook provider.

    The stored credentials are *chosen by the Dograh user* and mirrored into
    the Genesys Audio Connector integration: ``api_key`` (checked against the
    ``X-API-KEY`` upgrade header) and ``client_secret`` (RFC 9421 request
    signature verification; empty disables signed mode).
    """

    PROVIDER_NAME = WorkflowRunMode.GENESYS.value
    WEBHOOK_ENDPOINT = None  # Genesys connects out via WebSocket; no webhooks.

    def __init__(self, config: Dict[str, Any]):
        self.api_key = config.get("api_key", "")
        self.client_secret = config.get("client_secret", "")

    def validate_config(self) -> bool:
        return bool(self.api_key)

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        raise NotImplementedError(
            "Genesys AudioHook calls always originate from Genesys Cloud "
            "(Architect Call Audio Connector action); Dograh cannot place "
            "outbound calls through this provider."
        )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        return {
            "call_id": call_id,
            "status": "unknown",
            "error": "Genesys AudioHook does not expose call status to Dograh",
        }

    async def get_available_phone_numbers(self) -> List[str]:
        return []

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        return True

    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        logger.warning(
            "get_webhook_response called for Genesys - this should not happen. "
            "Genesys uses WebSocket connections, not webhooks."
        )
        return ""

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        return {
            "cost_usd": 0.0,
            "duration": 0,
            "status": "unknown",
            "error": "Genesys AudioHook does not provide cost information",
        }

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Map an AudioHook control message to the generic status shape."""
        return {
            "call_id": data.get("id", ""),
            "status": "unknown",
            "from_number": None,
            "to_number": None,
            "direction": "inbound",
            "duration": None,
            "extra": data,
        }

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
    ) -> None:
        """Run the pipeline on an already-accepted AudioHook WebSocket."""
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        logger.info(
            f"[Genesys] Starting pipeline for workflow_run {workflow_run_id}"
        )

        await run_pipeline_telephony(
            websocket,
            provider_name=self.PROVIDER_NAME,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            organization_id=organization_id,
            call_id="",
            transport_kwargs={},
        )

    async def handle_external_websocket(
        self,
        websocket: "WebSocket",
        *,
        organization_id: int,
        workflow_id: int,
        workflow_run_id: int,
        params: Dict[str, str],
    ) -> None:
        """Agent-stream entry point.

        URL ``/api/v1/agent-stream/genesys/{workflow_uuid}`` selects the agent.
        Auth mirrors the standard AudioHook route: ``X-API-KEY`` (upgrade
        header, with ``api_key`` query-param fallback for testing) must match a
        Genesys telephony configuration in ``organization_id``, and the RFC 9421
        signature is verified when that configuration carries a
        ``client_secret``.

        Unlike Cloudonix (``connected``/``start``), Genesys speaks AudioHook
        (``open``/``opened``). We peek at the first ``open`` message to stamp
        caller metadata on the run, then replay it into the pipeline so the
        serializer can complete its normal ``open`` → ``opened`` handshake.
        """
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        api_key = ""
        try:
            api_key = (websocket.headers.get("x-api-key") or "").strip()
        except Exception:
            api_key = ""
        if not api_key:
            api_key = (params.get("api_key") or "").strip()

        if not api_key:
            logger.warning("Genesys agent-stream missing X-API-KEY")
            await websocket.close(code=4401, reason="Missing X-API-KEY header")
            return

        config_row = await self._find_config_by_api_key(organization_id, api_key)
        if config_row is None:
            logger.warning(
                "Genesys agent-stream: no matching telephony configuration "
                f"for org {organization_id}"
            )
            await websocket.close(code=4401, reason="Unknown API key")
            return

        client_secret = ((config_row.credentials or {}).get("client_secret") or "")
        try:
            request_target = websocket.url.path + (
                f"?{websocket.url.query}" if websocket.url.query else ""
            )
        except Exception:
            request_target = ""
        try:
            result = verify_audiohook_signature(
                get_header=websocket.headers.get,
                get_header_list=websocket.headers.getlist,
                request_target=request_target,
                api_key=api_key,
                client_secret=client_secret,
            )
        except Exception as e:
            logger.warning(f"Genesys agent-stream signature check error: {e}")
            await websocket.close(code=4401, reason="Signature verification failed")
            return
        if not result.ok:
            logger.warning(
                f"Genesys agent-stream signature verification failed: {result.reason}"
            )
            await websocket.close(code=4401, reason="Signature verification failed")
            return

        try:
            try:
                raw_open = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=AGENT_STREAM_HANDSHAKE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Genesys agent-stream handshake timed out for workflow_run "
                    f"{workflow_run_id}"
                )
                await websocket.close(code=4408, reason="Handshake timeout")
                return

            text = raw_open.get("text") if isinstance(raw_open, dict) else None
            if raw_open.get("type") == "websocket.disconnect" or not text:
                logger.error("Genesys agent-stream expected open message first")
                await websocket.close(code=4400, reason="Expected open message")
                return
            try:
                open_msg = json.loads(text)
            except json.JSONDecodeError:
                logger.error("Genesys agent-stream received invalid JSON open")
                await websocket.close(code=4400, reason="Invalid open message")
                return
            if open_msg.get("type") != "open":
                logger.error(
                    f"Genesys agent-stream expected open, got: {open_msg.get('type')}"
                )
                await websocket.close(code=4400, reason="Expected open message")
                return

            open_params = open_msg.get("parameters") or {}
            session_id = open_msg.get("id") or ""
            conversation_id = open_params.get("conversationId") or ""
            participant = open_params.get("participant") or {}
            input_variables = open_params.get("inputVariables") or {}
            caller_number = participant.get("ani") or input_variables.get(
                "InputCallerID"
            )
            called_number = participant.get("dnis")
            try:
                genesys_org_id = websocket.headers.get(
                    "audiohook-organization-id", ""
                )
                correlation_id = websocket.headers.get(
                    "audiohook-correlation-id", ""
                )
                header_session_id = websocket.headers.get("audiohook-session-id", "")
            except Exception:
                genesys_org_id = ""
                correlation_id = ""
                header_session_id = ""
            call_id = session_id or header_session_id or correlation_id

            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                initial_context={
                    key: value
                    for key, value in {
                        "caller_number": caller_number,
                        "called_number": called_number,
                        "direction": "inbound",
                        "telephony_configuration_id": config_row.id,
                        "connector_id": params.get("connector_id") or "",
                    }.items()
                    if value is not None and value != ""
                },
                gathered_context={"call_id": call_id} if call_id else {},
                logs={
                    "audiohook": {
                        "genesys_organization_id": genesys_org_id,
                        "session_id": session_id or header_session_id,
                        "correlation_id": correlation_id,
                        "conversation_id": conversation_id,
                        "signed": result.signed,
                        "agent_stream": True,
                    },
                },
            )

            logger.info(
                f"Genesys agent-stream connected for workflow_run "
                f"{workflow_run_id} session={session_id} "
                f"conversation={conversation_id} "
                f"telephony_configuration_id={config_row.id}"
            )

            replay_socket = _ReplayWebSocket(websocket, [raw_open])
            await run_pipeline_telephony(
                replay_socket,
                provider_name=self.PROVIDER_NAME,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                organization_id=organization_id,
                call_id=call_id,
                transport_kwargs={},
            )
        except Exception as e:
            logger.error(f"Error in Genesys agent-stream handler: {e}")
            raise

    async def _find_config_by_api_key(self, organization_id: int, api_key: str):
        """Find the org's Genesys config matching the API key (tenant-scoped).

        Unlike the public ``/telephony/genesys`` route (which anchors on the
        API key across orgs because the workflow is unknown), agent-stream
        already knows the org from the agent UUID, so the lookup stays scoped —
        credentials from another org can never match.
        """
        configs = await db_client.list_telephony_configurations_by_provider(
            organization_id, self.PROVIDER_NAME
        )
        for config in configs:
            if (config.credentials or {}).get("api_key") == api_key:
                return config
        return None

    # ======== INBOUND CALL METHODS ========

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        return False

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        return NormalizedInboundData(
            provider=GenesysProvider.PROVIDER_NAME,
            call_id=webhook_data.get("id", ""),
            from_number="",
            to_number="",
            direction="inbound",
            call_status="inbound",
            account_id=None,
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        return config_data.get("api_key") == webhook_account_id

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        """Upgrade-request signature verification happens in routes.py; no HTTP webhooks."""
        return True

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data,
        backend_endpoint: str,
    ):
        """Genesys does not answer inbound webhooks with stream instructions."""
        return Response(content="", status_code=204)

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        return Response(
            content='{"error": "%s", "message": "%s"}' % (error_type, message),
            media_type="application/json",
        )

    @staticmethod
    def generate_validation_error_response(error_type) -> tuple:
        from api.errors.telephony_errors import TELEPHONY_ERROR_MESSAGES, TelephonyError

        message = TELEPHONY_ERROR_MESSAGES.get(
            error_type, TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED]
        )

        return Response(
            content='{"error": "%s", "message": "%s"}' % (str(error_type), message),
            media_type="application/json",
        )

    # ======== CALL TRANSFER METHODS ========

    def supports_transfers(self) -> bool:
        """Post-session routing is done by the Architect flow via outputVariables."""
        return False

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            "Genesys AudioHook does not support Dograh-initiated transfers; "
            "return transfer intent via outputVariables and let the Architect "
            "flow route the call."
        )


class _ReplayWebSocket:
    """Replay buffered ASGI messages before delegating to the real socket.

    The agent-stream handshake peeks at the AudioHook ``open`` message to
    stamp run metadata, but the pipeline serializer still needs to see it to
    complete ``open`` → ``opened``. Buffer the raw ``receive()`` dicts and
    serve them first; everything else (``send_*``, ``headers``, ``url``,
    ``close``) delegates to the wrapped WebSocket.
    """

    def __init__(self, wrapped: "WebSocket", buffered: list):
        self._wrapped = wrapped
        self._buffered = list(buffered)

    async def receive(self):
        if self._buffered:
            return self._buffered.pop(0)
        return await self._wrapped.receive()

    def __getattr__(self, name: str):
        return getattr(self._wrapped, name)
