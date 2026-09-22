"""Tests for Genesys AudioHook RFC 9421 upgrade-request signature verification."""

import base64
import hashlib
import hmac

import pytest

from api.services.telephony.providers.genesys import security
from api.services.telephony.providers.genesys.security import (
    SignatureVerifyResult,
    verify_audiohook_signature,
)

SECRET = "unit-test-secret"
API_KEY = "my-key"
NOW = 1756540010.0
REQUEST_TARGET = "/api/v1/telephony/genesys/6f1c2e34-uuid"

COMPONENTS = [
    "@request-target",
    "@authority",
    "audiohook-organization-id",
    "audiohook-session-id",
    "audiohook-correlation-id",
    "x-api-key",
]

BASE_HEADERS = {
    "host": "dograh.example.com",
    "x-api-key": API_KEY,
    "audiohook-organization-id": "org-123",
    "audiohook-session-id": "sess-456",
    "audiohook-correlation-id": "corr-789",
}


def _component_value(component: str, x_api_key: str) -> str:
    if component == "@request-target":
        return REQUEST_TARGET
    if component == "@authority":
        return BASE_HEADERS["host"]
    if component == "x-api-key":
        return x_api_key
    return BASE_HEADERS[component]


def _build_signature(
    *,
    sig_params: dict | None = None,
    request_target: str = REQUEST_TARGET,
    x_api_key_base_value: str = API_KEY,
    secret: str = SECRET,
) -> tuple[str, str]:
    params = sig_params or {
        "nonce": "n" * 22,
        "created": int(NOW) - 2,
        "keyid": API_KEY,
        "alg": "hmac-sha256",
    }
    params_str = security._serialize_inner_list(
        [(c, {}) for c in COMPONENTS], params
    )
    lines = [f'"{c}": {_component_value(c, x_api_key_base_value)}' for c in COMPONENTS]
    lines.append(f'"@signature-params": {params_str}')
    base = "\n".join(lines)
    digest = hmac.new(secret.encode(), base.encode(), hashlib.sha256).digest()
    sig = base64.b64encode(digest).decode()
    return f"sig1={params_str}", f"sig1=:{sig}:"


def _headers_env(headers: dict, secret: str = SECRET) -> SignatureVerifyResult:
    get_header = lambda name: (
        ",".join(headers[name]) if isinstance(headers.get(name), list) else headers.get(name)
    )
    get_header_list = lambda name: (
        headers[name]
        if isinstance(headers.get(name), list)
        else ([headers[name]] if name in headers else [])
    )
    return verify_audiohook_signature(
        get_header=get_header,
        get_header_list=get_header_list,
        request_target=REQUEST_TARGET,
        api_key=API_KEY,
        client_secret=secret,
        now=NOW,
    )


def test_parse_dictionary_inner_list_and_params():
    parsed = security.parse_dictionary(
        'sig1=("@request-target" "@authority" "x-api-key");'
        'nonce="abc123456789abc1234567";created=1756540000;keyid="my-key";alg="hmac-sha256"'
    )
    items, params = parsed["sig1"]
    assert [c for c, _ in items] == ["@request-target", "@authority", "x-api-key"]
    assert params["created"] == 1756540000
    assert params["keyid"] == "my-key"
    assert params["alg"] == "hmac-sha256"
    assert params["nonce"] == "abc123456789abc1234567"


def test_parse_dictionary_byte_sequence():
    parsed = security.parse_dictionary("sig1=:aGVsbG8=:")
    assert parsed["sig1"][0] == b"hello"


def test_unsigned_request_accepted_without_secret():
    result = _headers_env(dict(BASE_HEADERS), secret="")
    assert result.ok
    assert not result.signed


def test_valid_signature_verifies():
    sig_input, sig_header = _build_signature()
    headers = dict(BASE_HEADERS, **{"signature-input": sig_input, "signature": sig_header})
    result = _headers_env(headers)
    assert result.ok, result.reason
    assert result.signed


def test_tampered_signature_rejected():
    sig_input, sig_header, raw = _build_signature_with_raw()
    bad = sig_header.replace(raw[:8], "AAAA" if raw[:4] != "AAAA" else "BBBB")
    headers = dict(BASE_HEADERS, **{"signature-input": sig_input, "signature": bad})
    result = _headers_env(headers)
    assert not result.ok
    assert result.signed


def _build_signature_with_raw() -> tuple[str, str, str]:
    params = {
        "nonce": "n" * 22,
        "created": int(NOW) - 2,
        "keyid": API_KEY,
        "alg": "hmac-sha256",
    }
    params_str = security._serialize_inner_list([(c, {}) for c in COMPONENTS], params)
    lines = [f'"{c}": {_component_value(c, API_KEY)}' for c in COMPONENTS]
    lines.append(f'"@signature-params": {params_str}')
    base = "\n".join(lines)
    digest = hmac.new(SECRET.encode(), base.encode(), hashlib.sha256).digest()
    raw = base64.b64encode(digest).decode()
    return f"sig1={params_str}", f"sig1=:{raw}:", raw


def test_wrong_request_target_rejected():
    sig_input, sig_header = _build_signature()
    headers = dict(BASE_HEADERS, **{"signature-input": sig_input, "signature": sig_header})
    result = verify_audiohook_signature(
        get_header=lambda n: headers.get(n),
        get_header_list=lambda n: [headers[n]] if n in headers else [],
        request_target="/other/path",
        api_key=API_KEY,
        client_secret=SECRET,
        now=NOW,
    )
    assert not result.ok


def test_stale_signature_rejected():
    sig_input, sig_header = _build_signature()
    headers = dict(BASE_HEADERS, **{"signature-input": sig_input, "signature": sig_header})
    result = verify_audiohook_signature(
        get_header=lambda n: headers.get(n),
        get_header_list=lambda n: [headers[n]] if n in headers else [],
        request_target=REQUEST_TARGET,
        api_key=API_KEY,
        client_secret=SECRET,
        now=NOW + 60,
    )
    assert not result.ok


def test_keyid_api_key_mismatch_rejected():
    sig_input, sig_header = _build_signature()
    headers = dict(BASE_HEADERS, **{"signature-input": sig_input, "signature": sig_header})
    result = verify_audiohook_signature(
        get_header=lambda n: headers.get(n),
        get_header_list=lambda n: [headers[n]] if n in headers else [],
        request_target=REQUEST_TARGET,
        api_key="different-key",
        client_secret=SECRET,
        now=NOW,
    )
    assert not result.ok


def test_multi_value_header_joined_and_verified():
    sig_input, sig_header = _build_signature(x_api_key_base_value=f"{API_KEY}, extra")
    headers = dict(BASE_HEADERS)
    headers["x-api-key"] = [API_KEY, "extra"]
    headers["signature-input"] = sig_input
    headers["signature"] = sig_header
    result = _headers_env(headers)
    assert result.ok, result.reason
    assert result.signed


def test_unparseable_signature_input_rejected():
    headers = dict(BASE_HEADERS, **{"signature-input": "sig1=(unbalanced"})
    result = _headers_env(headers)
    assert not result.ok


def test_missing_nonce_rejected():
    sig_input, sig_header = _build_signature(sig_params={"created": int(NOW) - 2, "keyid": API_KEY})
    headers = dict(BASE_HEADERS, **{"signature-input": sig_input, "signature": sig_header})
    result = _headers_env(headers)
    assert not result.ok


def test_short_nonce_rejected():
    sig_input, sig_header = _build_signature(
        sig_params={"nonce": "short", "created": int(NOW) - 2, "keyid": API_KEY}
    )
    headers = dict(BASE_HEADERS, **{"signature-input": sig_input, "signature": sig_header})
    result = _headers_env(headers)
    assert not result.ok
