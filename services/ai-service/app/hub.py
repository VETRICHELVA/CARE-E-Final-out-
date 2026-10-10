"""The AI service's only way into the hub: GET /ai/read/* with the AI service token, on behalf
of the signed-in user (their access token in X-On-Behalf-Of). The hub applies that user's org
scope, capabilities and redaction. There is no write method here on purpose."""

from dataclasses import dataclass
from typing import Any

import httpx


class HubUnauthenticated(Exception):
    """The hub refused the user's token (expired or signed out): the caller must sign in or
    refresh, so this surfaces as a 401 instead of an answer."""


class HubUnavailable(Exception):
    """The hub could not be reached, or failed (5xx)."""


@dataclass(frozen=True)
class HubResult:
    ok: bool
    status: int
    data: Any  # the JSON body when ok, else the hub's {code, message, details}


class HubReader:
    def __init__(
        self, base_url: str, service_token: str, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._base = base_url.rstrip("/")
        self._token = service_token

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(
        self, path: str, user_token: str, params: dict[str, str] | None = None
    ) -> HubResult:
        """GET {hub}/ai/read/{path} as the user. 403 and 404 come back as a failed result
        (the copilot says the data is not available); 401 and 5xx raise."""
        url = f"{self._base}/ai/read/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._token}", "X-On-Behalf-Of": user_token}
        try:
            r = await self._client.get(url, headers=headers, params=params)
        except httpx.HTTPError as e:
            raise HubUnavailable(str(e)) from e
        if r.status_code == 401:
            if "AI service token" in r.text:  # our own token: a deployment error, not the user's
                raise HubUnavailable("the hub refused the AI service token")
            raise HubUnauthenticated(r.text)
        if r.status_code >= 500:
            raise HubUnavailable(f"hub returned {r.status_code}")
        try:
            body = r.json()
        except ValueError as e:
            raise HubUnavailable("hub returned a non-JSON body") from e
        return HubResult(ok=r.is_success, status=r.status_code, data=body)
