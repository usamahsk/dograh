"""Genesys Cloud CX AudioHook implementation of the TelephonyProvider interface.

Genesys Cloud dials *out* to Dograh: the Architect "Call Audio Connector"
action opens a WebSocket to the endpoint exposed by this package's routes
(``/api/v1/telephony/genesys/{connector_id}``) and streams AudioHook protocol
messages. There are no HTTP webhooks and Dograh cannot originate calls on
Genesys; sessions always begin with an inbound AudioHook connection.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi import Response
from loguru import logger

from api.enums import WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    TelephonyProvider,
)

if TYPE_CHECKING:
    from fastapi import WebSocket


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
