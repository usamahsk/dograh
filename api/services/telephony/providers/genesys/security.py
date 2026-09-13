"""AudioHook WebSocket upgrade signature verification.

Genesys Cloud signs the AudioHook WebSocket upgrade request using the RFC 9421
"Signature-Input" / "Signature" header mechanism (RFC 8941 structured fields)
with HMAC-SHA256 over the client secret configured on the Audio Connector
integration. Covered components per the AudioHook spec:

    "@request-target", "@authority", "audiohook-organization-id",
    "audiohook-session-id", "audiohook-correlation-id", "x-api-key"

Behavior mirrors the official reference implementation
(GenesysCloudBlueprints/audioconnector-server-reference-implementation):

- Missing "X-API-KEY" -> reject.
- Missing "Signature-Input" -> unsigned; the caller decides policy (the
  reference implementation accepts unsigned requests).
- Present but invalid signature -> reject.

Only the subset of RFC 8941 needed for AudioHook signatures is implemented.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Tuple, Union

# Components Genesys Cloud signs on every AudioHook upgrade request.
REQUIRED_COMPONENTS = (
    "@request-target",
    "@authority",
    "audiohook-organization-id",
    "audiohook-session-id",
    "audiohook-correlation-id",
    "x-api-key",
)

SUPPORTED_ALGORITHM = "hmac-sha256"

# Maximum age (seconds) of the signature "created" timestamp. Matches the
# reference implementation's maxSignatureAge.
DEFAULT_MAX_SIGNATURE_AGE = 10

SfValue = Union[str, int, float, bool, bytes]
# An inner list: [(component-id, per-item params), ...] plus shared params.
InnerList = Tuple[List[Tuple[str, Dict[str, SfValue]]], Dict[str, SfValue]]


class StructuredFieldError(ValueError):
    """Raised when a structured-field header cannot be parsed."""


class _Parser:
    """Minimal RFC 8941 parser (dictionary -> inner lists / items / params)."""

    _TOKEN_CHARS = set(
        "!#$%&'*+-.^_`|~0123456789"
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    )

    def __init__(self, text: str):
        self._text = text
        self._pos = 0

    def parse_dictionary(self) -> Dict[str, Union[InnerList, Tuple[SfValue, Dict[str, SfValue]]]]:
        result: Dict[str, Union[InnerList, Tuple[SfValue, Dict[str, SfValue]]]] = {}
        first = True
        while self._pos < len(self._text):
            if not first:
                self._skip_sp()
                if self._peek() != ",":
                    raise StructuredFieldError("expected ',' between dictionary entries")
                self._advance()
                self._skip_sp()
            first = False
            key = self._parse_key()
            if self._peek() == "=":
                self._advance()
                if self._peek() == "(":
                    value: Union[InnerList, Tuple[SfValue, Dict[str, SfValue]]] = (
                        self._parse_inner_list()
                    )
                else:
                    value = self._parse_item()
            else:
                value = (True, {})
            result[key] = value
        return result

    def _parse_inner_list(self) -> InnerList:
        self._advance()  # consume "("
        items: List[Tuple[str, Dict[str, SfValue]]] = []
        while True:
            self._skip_sp()
            if self._peek() is None:
                raise StructuredFieldError("unterminated inner list")
            if self._peek() == ")":
                self._advance()
                break
            item_value, item_params = self._parse_item()
            items.append((item_value, item_params))
        params = self._parse_parameters()
        return items, params

    def _parse_item(self) -> Tuple[SfValue, Dict[str, SfValue]]:
        value = self._parse_bare_item()
        params = self._parse_parameters()
        return value, params

    def _parse_bare_item(self) -> SfValue:
        ch = self._peek()
        if ch is None:
            raise StructuredFieldError("unexpected end of input")
        if ch == '"':
            return self._parse_string()
        if ch == ":":
            return self._parse_byte_sequence()
        if ch == "?":
            return self._parse_boolean()
        if ch == "-" or ch.isdigit():
            return self._parse_number()
        if ch.isalpha() or ch == "*":
            return self._parse_token()
        raise StructuredFieldError(f"unexpected character {ch!r}")

    def _parse_key(self) -> str:
        start = self._pos
        while self._pos < len(self._text) and (
            self._text[self._pos].islower()
            or self._text[self._pos].isdigit()
            or self._text[self._pos] in "_-"
        ):
            self._pos += 1
        if self._pos == start:
            raise StructuredFieldError("expected dictionary key")
        return self._text[start : self._pos]

    def _parse_parameters(self) -> Dict[str, SfValue]:
        params: Dict[str, SfValue] = {}
        while self._peek() == ";":
            self._advance()
            self._skip_sp()
            key = self._parse_key()
            if self._peek() == "=":
                self._advance()
                params[key] = self._parse_bare_item()
            else:
                params[key] = True
        return params

    def _parse_string(self) -> str:
        self._advance()  # consume opening quote
        out: List[str] = []
        while True:
            ch = self._peek()
            if ch is None:
                raise StructuredFieldError("unterminated string")
            if ch == "\\":
                self._advance()
                escaped = self._peek()
                if escaped not in ('"', "\\"):
                    raise StructuredFieldError("invalid string escape")
                out.append(escaped)
                self._advance()
            elif ch == '"':
                self._advance()
                return "".join(out)
            else:
                out.append(ch)
                self._advance()

    def _parse_token(self) -> str:
        start = self._pos
        while self._pos < len(self._text) and self._text[self._pos] in self._TOKEN_CHARS:
            self._pos += 1
        if self._pos == start:
            raise StructuredFieldError("expected token")
        return self._text[start : self._pos]

    def _parse_byte_sequence(self) -> bytes:
        self._advance()  # consume ":"
        end = self._text.find(":", self._pos)
        if end == -1:
            raise StructuredFieldError("unterminated byte sequence")
        raw = self._text[self._pos : end]
        self._pos = end + 1
        try:
            return base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as e:
            raise StructuredFieldError("invalid base64 byte sequence") from e

    def _parse_boolean(self) -> bool:
        self._advance()  # consume "?"
        ch = self._peek()
        if ch not in ("0", "1"):
            raise StructuredFieldError("invalid boolean")
        self._advance()
        return ch == "1"

    def _parse_number(self) -> Union[int, float]:
        start = self._pos
        if self._peek() == "-":
            self._advance()
        is_decimal = False
        while self._pos < len(self._text) and (
            self._text[self._pos].isdigit() or (self._text[self._pos] == "." and not is_decimal)
        ):
            if self._text[self._pos] == ".":
                is_decimal = True
            self._pos += 1
        text = self._text[start : self._pos]
        if not text or text == "-":
            raise StructuredFieldError("invalid number")
        return float(text) if is_decimal else int(text)

    def _skip_sp(self) -> None:
        while self._pos < len(self._text) and self._text[self._pos] == " ":
            self._pos += 1

    def _peek(self):
        return self._text[self._pos] if self._pos < len(self._text) else None

    def _advance(self) -> None:
        if self._pos >= len(self._text):
            raise StructuredFieldError("unexpected end of structured field")
        self._pos += 1


def _serialize_value(value: SfValue) -> str:
    if isinstance(value, bool):
        return "?1" if value else "?0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, bytes):
        return f":{base64.b64encode(value).decode()}:"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _serialize_params(params: Mapping[str, SfValue]) -> str:
    parts = []
    for key, value in params.items():
        parts.append(f";{key}" if value is True else f";{key}={_serialize_value(value)}")
    return "".join(parts)


def _serialize_inner_list(items: List[Tuple[str, Dict[str, SfValue]]], params: Mapping[str, SfValue]) -> str:
    rendered = []
    for value, item_params in items:
        rendered.append(_serialize_value(value) + _serialize_params(item_params))
    return "(" + " ".join(rendered) + ")" + _serialize_params(params)


def parse_dictionary(text: str) -> Dict[str, Union[InnerList, Tuple[SfValue, Dict[str, SfValue]]]]:
    """Parse an RFC 8941 dictionary header value."""
    return _Parser(text).parse_dictionary()


@dataclass
class SignatureVerifyResult:
    ok: bool
    reason: str = ""
    signed: bool = False  # True when a Signature-Input header was present
    covered_components: List[str] = field(default_factory=list)


def verify_audiohook_signature(
    *,
    get_header: Callable[[str], str | None],
    get_header_list: Callable[[str], List[str]],
    request_target: str,
    api_key: str,
    client_secret: str,
    now: float | None = None,
    max_age: int = DEFAULT_MAX_SIGNATURE_AGE,
) -> SignatureVerifyResult:
    """Verify an AudioHook upgrade request signature (RFC 9421, HMAC-SHA256).

    Args:
        get_header: Single (joined) header value lookup, lowercase name.
        get_header_list: All values of a header, lowercase name, in order.
        request_target: Origin-form request target (path + query) that the
            client signed, i.e. the ``@request-target`` derived component.
        api_key: Expected value of the ``X-API-KEY`` header.
        client_secret: Shared secret used for the HMAC. Empty disables
            signature checking (unsigned mode, matching the reference
            implementation).
        now: Current unix timestamp (overridable for tests).
        max_age: Maximum allowed age of the signature.

    Returns:
        SignatureVerifyResult with ok/reason and whether the request was signed.
    """
    signature_input_header = get_header("signature-input")
    if not signature_input_header or not client_secret:
        return SignatureVerifyResult(ok=True, reason="unsigned")

    try:
        signature_inputs = parse_dictionary(signature_input_header)
    except StructuredFieldError as e:
        return SignatureVerifyResult(ok=False, reason=f"Unparseable Signature-Input: {e}", signed=True)

    signature_header = get_header("signature")
    if not signature_header:
        return SignatureVerifyResult(ok=False, reason='Missing "Signature" header field', signed=True)

    try:
        signatures = parse_dictionary(signature_header)
    except StructuredFieldError as e:
        return SignatureVerifyResult(ok=False, reason=f"Unparseable Signature: {e}", signed=True)

    current_time = time.time() if now is None else now

    for label, parsed in signature_inputs.items():
        if label not in signatures:
            continue
        items, params = parsed
        if not isinstance(items, list):
            continue

        components = [component for component, _ in items]
        failure = _check_signature_params(params, api_key, current_time, max_age)
        if failure:
            continue

        covered = _collect_covered_components(components)
        if not set(REQUIRED_COMPONENTS).issubset(covered):
            continue

        base = _build_signature_base(
            items, params, get_header_list, request_target
        )
        if base is None:
            continue

        signature_bytes = signatures[label][0]
        if not isinstance(signature_bytes, bytes):
            continue
        expected = hmac.new(
            client_secret.encode(), base.encode(), hashlib.sha256
        ).digest()
        if hmac.compare_digest(expected, signature_bytes):
            return SignatureVerifyResult(
                ok=True, signed=True, covered_components=covered
            )

    return SignatureVerifyResult(
        ok=False,
        reason="No valid signature present",
        signed=True,
        covered_components=[],
    )


def _check_signature_params(
    params: Mapping[str, SfValue], api_key: str, now: float, max_age: int
) -> str:
    """Return a failure reason for the signature parameters, or "" when valid."""
    nonce = params.get("nonce")
    if not isinstance(nonce, str) or len(nonce) < 22:
        return 'Missing or too-small "nonce" signature parameter'

    keyid = params.get("keyid")
    if keyid != api_key:
        return 'X-API-KEY header field and signature keyid mismatch'

    alg = params.get("alg")
    if alg is not None and alg != SUPPORTED_ALGORITHM:
        return f"Unsupported signature algorithm: {alg}"

    created = params.get("created")
    if not isinstance(created, int):
        return 'Missing "created" signature parameter'
    if created > now + max_age:
        return "Signature created timestamp is in the future"
    if now - created > max_age:
        return "Signature has expired"

    expires = params.get("expires")
    if isinstance(expires, int) and expires < now:
        return "Signature has expired"

    return ""


def _collect_covered_components(components: List[str]) -> List[str]:
    return [c for c in components if isinstance(c, str)]


def _build_signature_base(
    items: List[Tuple[str, Dict[str, SfValue]]],
    params: Mapping[str, SfValue],
    get_header_list: Callable[[str], List[str]],
    request_target: str,
) -> str | None:
    """Build the RFC 9421 signature base; None if a component can't be resolved."""
    lines: List[str] = []
    for component, _ in items:
        if component == "@request-target":
            value = request_target
        elif component == "@authority":
            values = get_header_list("host")
            if not values:
                return None
            value = ", ".join(v.strip() for v in values)
        elif component.startswith("@"):
            return None  # unsupported derived component
        else:
            values = get_header_list(component.lower())
            if not values:
                return None
            value = ", ".join(v.strip() for v in values)
        lines.append(f'"{component}": {value}')
    lines.append(f'"@signature-params": {_serialize_inner_list(items, params)}')
    return "\n".join(lines)
