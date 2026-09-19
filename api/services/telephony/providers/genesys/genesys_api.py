"""Genesys Cloud REST API client (OAuth client-credentials).

Used by the session logger to push live call data onto the Genesys
conversation as participant attributes. Credentials come from the org's
Genesys telephony configuration (never from code).
"""

import time
from typing import Any, Dict, Optional, Tuple

import aiohttp
from loguru import logger

_TOKEN_CACHE: Dict[Tuple[str, str], Tuple[str, float]] = {}
_TOKEN_TTL_SECONDS = 30 * 60  # refresh 5 min before the ~1h expiry margin


class GenesysApiClient:
    """Thin async client for login/oauth + conversations API."""

    def __init__(self, client_id: str, client_secret: str, region: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region.strip()  # e.g. "usw2.pure.cloud"
        # Throttle repeated failure logs (e.g. invalid credentials) so live
        # pushes don't spam the logs — warn at most once per minute.
        self._last_auth_error_log = 0.0

    @property
    def _api_base(self) -> str:
        return f"https://api.{self.region}"

    def _log_auth_error(self, message: str) -> None:
        self._log_throttled(message)

    def _log_throttled(self, message: str) -> None:
        now = time.time()
        if now - self._last_auth_error_log >= 60:
            logger.warning(message)
            self._last_auth_error_log = now

    async def _get_token(self) -> Optional[str]:
        cache_key = (self.client_id, self.region)
        cached = _TOKEN_CACHE.get(cache_key)
        if cached and cached[1] > time.time():
            return cached[0]

        token_url = f"https://login.{self.region}/oauth/token"
        auth = aiohttp.BasicAuth(self.client_id, self.client_secret)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    token_url,
                    data="grant_type=client_credentials",
                    auth=auth,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status != 200:
                        body = await response.text()
                        self._log_auth_error(
                            f"Genesys OAuth failed ({response.status}): {body[:200]}"
                        )
                        return None
                    data = await response.json()
            token = data.get("access_token")
            if not token:
                logger.warning("Genesys OAuth response missing access_token")
                return None
            expires_in = float(data.get("expires_in", 3600))
            _TOKEN_CACHE[cache_key] = (
                token,
                time.time() + max(expires_in - 300, 60),
            )
            return token
        except Exception as e:
            logger.warning(f"Genesys OAuth error: {e}")
            return None

    async def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        token = await self._get_token()
        if not token:
            return None
        url = f"{self._api_base}/api/v2/conversations/{conversation_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status != 200:
                        body = await response.text()
                        logger.warning(
                            f"Genesys get_conversation failed ({response.status}): {body[:200]}"
                        )
                        return None
                    return await response.json()
        except Exception as e:
            logger.warning(f"Genesys get_conversation error: {e}")
            return None

    async def find_customer_participant_id(self, conversation_id: str) -> Optional[str]:
        """The external/customer participant owns the conversation attributes."""
        conversation = await self.get_conversation(conversation_id)
        if not conversation:
            return None
        for participant in conversation.get("participants", []):
            if participant.get("purpose") in ("external", "customer", "user"):
                return participant.get("id")
        logger.warning(
            f"Genesys conversation {conversation_id} has no customer participant"
        )
        return None

    async def update_participant_attributes(
        self, conversation_id: str, participant_id: str, attributes: Dict[str, Any]
    ) -> bool:
        token = await self._get_token()
        if not token:
            return False
        url = (
            f"{self._api_base}/api/v2/conversations/{conversation_id}"
            f"/participants/{participant_id}/attributes"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.patch(
                    url,
                    json={"attributes": attributes},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    # Genesys returns 202 Accepted with the updated attributes.
                    if response.status not in (200, 201, 202, 204):
                        body = await response.text()
                        self._log_throttled(
                            f"Genesys update_attributes failed ({response.status}): {body[:200]}"
                        )
                        return False
                    return True
        except Exception as e:
            logger.warning(f"Genesys update_attributes error: {e}")
            return False
