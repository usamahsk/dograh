"""Genesys AudioHook WebSocket endpoint.

Genesys Cloud connects here directly: the Audio Connector integration's Base
Connection URI is ``wss://<host>/api/v1/telephony/genesys`` and Genesys
appends the Architect action's Connector ID as a path segment. We use the
Dograh agent's ``workflow_uuid`` as the Connector ID.

Handshake authentication (per the AudioHook spec):

- ``X-API-KEY`` header must match the ``api_key`` stored on a Genesys
  telephony configuration owned by the target workflow's organization.
- If the stored configuration carries a ``client_secret``, the RFC 9421
  signature headers are required and verified (HMAC-SHA256 over the signed
  components). Without a stored secret, unsigned requests are accepted —
  same policy as the official reference implementation.
"""

import uuid

from fastapi import APIRouter, WebSocket
from loguru import logger
from pipecat.utils.run_context import set_current_org_id, set_current_run_id
from starlette.websockets import WebSocketDisconnect

from api.db import db_client
from api.enums import CallType, WorkflowRunState
from api.services.quota_service import authorize_workflow_run_start
from api.services.telephony.factory import get_telephony_provider_by_id

from .provider import GenesysProvider
from .security import verify_audiohook_signature

import asyncio
import json
import uuid

from fastapi import APIRouter, WebSocket
from loguru import logger
from pipecat.utils.run_context import set_current_org_id, set_current_run_id
from starlette.websockets import WebSocketDisconnect

from api.db import db_client
from api.enums import CallType, WorkflowRunState
from api.services.quota_service import authorize_workflow_run_start
from api.services.telephony.factory import get_telephony_provider_by_id

from .provider import GenesysProvider
from .security import verify_audiohook_signature

router = APIRouter()

_VALIDATION_TIMEOUT_SECONDS = 30


@router.websocket("/genesys")
async def genesys_audiohook_validation_websocket(websocket: WebSocket):
    """Handshake-only endpoint for integration activation validation.

    Genesys validates the Audio Connector integration by connecting to the
    Base Connection URI *without* appending a Connector ID. There is no
    workflow bound to such a session, so complete the AudioHook handshake
    (open→opened, ping→pong, close→closed) and stop — never start a pipeline.
    """
    await websocket.accept()
    logger.info("Genesys validation socket connected (bare Base URI)")
    seq = 0
    session_id = websocket.headers.get("audiohook-session-id", str(uuid.uuid4()))

    def _reply(msg_type: str, parameters: dict | None = None) -> dict:
        nonlocal seq
        seq += 1
        return {
            "version": "2",
            "type": msg_type,
            "seq": seq,
            "clientseq": 0,
            "id": session_id,
            "parameters": parameters or {},
        }

    try:
        while True:
            data = await asyncio.wait_for(
                websocket.receive(), timeout=_VALIDATION_TIMEOUT_SECONDS
            )
            if data.get("type") == "websocket.disconnect":
                break
            text = data.get("text")
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                continue
            msg_type = message.get("type")
            if msg_type == "open":
                await websocket.send_json(
                    _reply(
                        "opened",
                        {
                            "startPaused": False,
                            "media": [
                                {
                                    "type": "audio",
                                    "format": "PCMU",
                                    "channels": ["external"],
                                    "rate": 8000,
                                }
                            ],
                        },
                    )
                )
                logger.info("Genesys validation handshake: sent opened")
            elif msg_type == "ping":
                await websocket.send_json(_reply("pong"))
            elif msg_type == "close":
                await websocket.send_json(_reply("closed"))
                logger.info("Genesys validation handshake: closed by client")
                break
    except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
        pass

    try:
        await websocket.close()
    except RuntimeError:
        pass


@router.websocket("/genesys/{connector_id}")
async def genesys_audiohook_websocket(websocket: WebSocket, connector_id: str):
    """AudioHook entry point for Genesys Cloud CX.

    ``connector_id`` is the Dograh agent's ``workflow_uuid`` configured as the
    Connector ID in the Genesys Architect Call Audio Connector action.
    """
    await websocket.accept()

    api_key = websocket.headers.get("x-api-key", "")
    session_id = websocket.headers.get("audiohook-session-id", "")
    correlation_id = websocket.headers.get("audiohook-correlation-id", "")
    genesys_org_id = websocket.headers.get("audiohook-organization-id", "")

    if not api_key:
        logger.warning("Genesys WebSocket missing X-API-KEY header")
        await websocket.close(code=4401, reason="Missing X-API-KEY header")
        return

    workflow = await db_client.get_workflow_by_uuid_unscoped(connector_id)
    config_row = None

    # Tenant anchor is the API key: it identifies the org's Genesys config.
    # If the Connector ID is a real agent UUID in that org, it wins
    # (per-Genesys-action routing). Otherwise the config's
    # default_workflow_uuid is used, so the agent is switchable from Dograh
    # without touching the Architect flow.
    if workflow and workflow.organization_id:
        config_row = await _find_config_by_api_key(workflow.organization_id, api_key)

    if config_row is None:
        config_row = await _find_config_by_api_key_any_org(api_key)

    if config_row is None:
        def _mask(value: str | None) -> str:
            return f"len={len(value)} prefix={value[:3]}***" if value else "EMPTY"

        logger.warning(
            f"Genesys WebSocket: no matching telephony configuration for "
            f"X-API-KEY {_mask(api_key)}"
        )
        await websocket.close(code=4401, reason="Unknown API key")
        return

    organization_id = config_row.organization_id

    if not workflow or workflow.organization_id != organization_id:
        default_uuid = (config_row.credentials or {}).get("default_workflow_uuid", "")
        if default_uuid:
            workflow = await db_client.get_workflow_by_uuid_unscoped(default_uuid)
        if not workflow or workflow.organization_id != organization_id:
            logger.warning(
                f"Genesys WebSocket: unknown connector_id '{connector_id}' and no "
                f"valid default workflow for org {organization_id}"
            )
            await websocket.close(code=4404, reason="Unknown connector ID")
            return

    client_secret = (config_row.credentials or {}).get("client_secret", "")
    result = verify_audiohook_signature(
        get_header=websocket.headers.get,
        get_header_list=websocket.headers.getlist,
        request_target=websocket.url.path
        + (f"?{websocket.url.query}" if websocket.url.query else ""),
        api_key=api_key,
        client_secret=client_secret,
    )
    if not result.ok:
        logger.warning(
            f"Genesys WebSocket signature verification failed: {result.reason}"
        )
        await websocket.close(code=4401, reason="Signature verification failed")
        return

    logger.info(
        f"Genesys AudioHook connected: workflow={workflow.id} "
        f"config={config_row.id} genesys_org={genesys_org_id} "
        f"session={session_id} correlation={correlation_id} "
        f"signed={result.signed}"
    )

    numeric_suffix = int(str(uuid.uuid4()).replace("-", "")[:8], 16) % 100000000
    workflow_run_name = f"WR-GS-{numeric_suffix:08d}"
    call_id = session_id or correlation_id
    initial_context = {
        **(workflow.template_context_variables or {}),
        "provider": GenesysProvider.PROVIDER_NAME,
        "caller_number": None,
        "called_number": None,
        "direction": "inbound",
        "telephony_configuration_id": config_row.id,
        "connector_id": connector_id,
    }
    workflow_run = await db_client.create_workflow_run(
        workflow_run_name,
        workflow.id,
        GenesysProvider.PROVIDER_NAME,
        user_id=workflow.user_id,
        call_type=CallType.INBOUND,
        initial_context=initial_context,
        gathered_context={"call_id": call_id} if call_id else {},
        logs={
            "audiohook": {
                "genesys_organization_id": genesys_org_id,
                "session_id": session_id,
                "correlation_id": correlation_id,
                "signed": result.signed,
            },
        },
    )

    set_current_run_id(workflow_run.id)
    set_current_org_id(workflow.organization_id)

    quota_result = await authorize_workflow_run_start(
        workflow_id=workflow.id,
        workflow_run_id=workflow_run.id,
    )
    if not quota_result.has_quota:
        logger.warning(
            f"Genesys WebSocket quota exceeded for workflow {workflow.id}: "
            f"{quota_result.error_message}"
        )
        await websocket.close(
            code=1008, reason=quota_result.error_message or "Quota exceeded"
        )
        return

    await db_client.update_workflow_run(
        run_id=workflow_run.id, state=WorkflowRunState.RUNNING.value
    )

    try:
        provider = await get_telephony_provider_by_id(
            config_row.id, workflow.organization_id
        )
        await provider.handle_websocket(
            websocket, workflow.id, workflow.user_id, workflow_run.id
        )
    except WebSocketDisconnect as e:
        logger.info(
            f"Genesys WebSocket disconnected for run {workflow_run.id}: "
            f"code={e.code} reason={e.reason}"
        )
    except Exception as e:
        logger.error(f"Genesys WebSocket error for run {workflow_run.id}: {e}")
        try:
            await websocket.close(1011, "Internal server error")
        except RuntimeError:
            pass


async def _find_config_by_api_key(organization_id: int, api_key: str):
    """Find the org's Genesys telephony configuration matching the API key."""
    configs = await db_client.list_telephony_configurations_by_provider(
        organization_id, GenesysProvider.PROVIDER_NAME
    )
    for config in configs:
        if (config.credentials or {}).get("api_key") == api_key:
            return config
    return None


async def _find_config_by_api_key_any_org(api_key: str):
    """Find any org's Genesys config matching the API key (tenant anchor)."""
    configs = await db_client.list_all_telephony_configurations_by_provider(
        GenesysProvider.PROVIDER_NAME
    )
    for config in configs:
        if (config.credentials or {}).get("api_key") == api_key:
            return config
    return None
