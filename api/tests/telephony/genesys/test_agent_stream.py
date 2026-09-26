"""Tests for Genesys agent-stream (AudioHook over /agent-stream/genesys/...)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.telephony.providers.genesys.provider import GenesysProvider


class _FakeHeaders:
    def __init__(self, values):
        self._values = {k.lower(): v for k, v in values.items()}

    def get(self, name, default=None):
        return self._values.get(name.lower(), default)

    def getlist(self, name):
        value = self._values.get(name.lower())
        return [value] if value is not None else []


class _FakeURL:
    def __init__(self, path="/api/v1/agent-stream/genesys/agent-uuid", query=""):
        self.path = path
        self.query = query


class _FakeWebSocket:
    def __init__(self, *receive_messages, headers=None):
        self._receive = AsyncMock(side_effect=list(receive_messages))
        self.headers = _FakeHeaders(headers or {})
        self.url = _FakeURL()
        self.close = AsyncMock()
        self.send_json = AsyncMock()

    async def receive(self):
        return await self._receive()


def _open_message(session_id="sess-1", conversation_id="conv-1"):
    return {
        "type": "websocket.receive",
        "text": json.dumps(
            {
                "version": "2",
                "type": "open",
                "seq": 1,
                "clientseq": 0,
                "id": session_id,
                "parameters": {
                    "conversationId": conversation_id,
                    "participant": {"ani": "+15551230001", "dnis": "+15551230002"},
                    "media": [
                        {
                            "type": "audio",
                            "format": "PCMU",
                            "channels": ["external"],
                            "rate": 8000,
                        }
                    ],
                },
            }
        ),
    }


@pytest.mark.asyncio
async def test_agent_stream_verifies_api_key_and_replays_open():
    """Agent-stream stamps run metadata from AudioHook open, then replays it."""
    provider = GenesysProvider({})
    websocket = _FakeWebSocket(
        _open_message(),
        headers={
            "x-api-key": "secret-key",
            "audiohook-organization-id": "genesys-org",
            "audiohook-session-id": "sess-1",
            "audiohook-correlation-id": "corr-1",
            "host": "dograh.example.com",
        },
    )
    config = SimpleNamespace(id=9, credentials={"api_key": "secret-key"})
    provider._find_config_by_api_key = AsyncMock(return_value=config)

    with (
        patch(
            "api.services.telephony.providers.genesys.provider.db_client"
        ) as db_client,
        patch(
            "api.services.pipecat.run_pipeline.run_pipeline_telephony",
            new_callable=AsyncMock,
        ) as run_pipeline,
        patch(
            "api.services.telephony.providers.genesys.provider.verify_audiohook_signature"
        ) as verify,
    ):
        from api.services.telephony.providers.genesys.security import (
            SignatureVerifyResult,
        )

        verify.return_value = SignatureVerifyResult(ok=True, signed=False)
        db_client.update_workflow_run = AsyncMock()

        await provider.handle_external_websocket(
            websocket,
            organization_id=44,
            workflow_id=101,
            workflow_run_id=303,
            params={},
        )

    provider._find_config_by_api_key.assert_awaited_once_with(44, "secret-key")
    db_client.update_workflow_run.assert_awaited_once()
    _, kwargs = db_client.update_workflow_run.await_args
    assert kwargs["run_id"] == 303
    assert kwargs["initial_context"]["caller_number"] == "+15551230001"
    assert kwargs["initial_context"]["called_number"] == "+15551230002"
    assert kwargs["initial_context"]["telephony_configuration_id"] == 9
    assert kwargs["gathered_context"] == {"call_id": "sess-1"}
    assert kwargs["logs"]["audiohook"]["conversation_id"] == "conv-1"

    # Pipeline receives a replay socket that re-serves the buffered open.
    run_pipeline.assert_awaited_once()
    positional = run_pipeline.await_args.args
    run_kwargs = run_pipeline.await_args.kwargs
    assert run_kwargs["call_id"] == "sess-1"
    # run_pipeline_telephony called positionally with replay socket first.
    assert positional[0] is not websocket
    first = await positional[0].receive()
    assert json.loads(first["text"])["type"] == "open"
    websocket.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_stream_rejects_unknown_api_key():
    provider = GenesysProvider({})
    websocket = _FakeWebSocket(headers={"x-api-key": "bad-key"})
    provider._find_config_by_api_key = AsyncMock(return_value=None)

    await provider.handle_external_websocket(
        websocket,
        organization_id=44,
        workflow_id=101,
        workflow_run_id=303,
        params={},
    )

    websocket.close.assert_awaited_once()
    _, kwargs = websocket.close.await_args
    assert kwargs["code"] == 4401
