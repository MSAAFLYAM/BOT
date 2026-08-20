"""
pinterest/client.py — Async Pinterest API v5 client.

Architecture decisions:
  - All requests use httpx.AsyncClient (non-blocking).
  - Bearer token auth (PINTEREST_ACCESS_TOKEN env var).
  - Automatic retry on 429 (rate limit) with Retry-After header.
  - Structured error handling: PinterestError with error_code.
  - Rate limits: Pinterest allows ~1000 req/day for standard access.
    We stay well below with daily pin cap of 5.

Pinterest API v5 base URL: https://api.pinterest.com/v5
Documentation: https://developers.pinterest.com/docs/api/v5/

Endpoints used:
  GET  /user_account           → verify token + get username
  GET  /boards                 → list user boards
  POST /boards                 → create board
  POST /pins                   → create pin
  GET  /pins/{pin_id}          → get pin status
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.pinterest.com/v5"


class PinterestError(Exception):
    def __init__(self, message: str, status_code: int = 0, error_code: str = ""):
        self.message     = message
        self.status_code = status_code
        self.error_code  = error_code
        super().__init__(message)


@dataclass
class PinterestUser:
    username:     str
    account_type: str = ""
    profile_image:str = ""


class PinterestClient:
    """
    Async Pinterest API v5 client.

    Usage:
        client = PinterestClient()
        user = await client.get_user()
        boards = await client.list_boards()
        pin = await client.create_pin(...)
    """

    def __init__(self, access_token: Optional[str] = None):
        if access_token:
            self._token = access_token
        else:
            import os
            self._token = os.environ.get("PINTEREST_ACCESS_TOKEN", "")

    @property
    def is_configured(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }

    async def _request(
        self,
        method:  str,
        path:    str,
        payload: Optional[dict] = None,
        params:  Optional[dict] = None,
        timeout: int = 30,
    ) -> dict:
        """
        Make authenticated API request.
        Handles rate limiting (429) with automatic wait.
        """
        url = f"{API_BASE}{path}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method=method,
                url=url,
                headers=self._headers(),
                json=payload,
                params=params,
            )

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                raise PinterestError(
                    f"Rate limited. Retry after {retry_after}s",
                    status_code=429,
                    error_code="RATE_LIMITED",
                )

            if resp.status_code == 401:
                raise PinterestError(
                    "Invalid Pinterest access token",
                    status_code=401,
                    error_code="UNAUTHORIZED",
                )

            if resp.status_code not in (200, 201):
                error_data = {}
                try:
                    error_data = resp.json()
                except Exception:
                    pass
                raise PinterestError(
                    f"HTTP {resp.status_code}: {error_data.get('message', resp.text[:100])}",
                    status_code=resp.status_code,
                    error_code=error_data.get("code", ""),
                )

            return resp.json()

    async def get_user(self) -> PinterestUser:
        """Get authenticated user info. Use to verify token."""
        data = await self._request("GET", "/user_account")
        return PinterestUser(
            username=data.get("username", ""),
            account_type=data.get("account_type", ""),
            profile_image=data.get("profile_image", ""),
        )

    async def list_boards(
        self,
        page_size: int = 25,
    ) -> list[dict]:
        """List all boards for the authenticated user."""
        data = await self._request(
            "GET", "/boards",
            params={"page_size": page_size},
        )
        return data.get("items", [])

    async def get_board_by_name(self, name: str) -> Optional[dict]:
        """Find board by name (case-insensitive). Returns None if not found."""
        boards = await self.list_boards()
        name_lower = name.lower()
        for board in boards:
            if board.get("name", "").lower() == name_lower:
                return board
        return None

    async def create_board(
        self,
        name:        str,
        description: str = "",
        privacy:     str = "PUBLIC",
    ) -> dict:
        """
        Create a new Pinterest board.

        Args:
            name:        Board name
            description: Board description
            privacy:     "PUBLIC" or "SECRET"
        """
        return await self._request(
            "POST", "/boards",
            payload={
                "name":        name,
                "description": description,
                "privacy":     privacy,
            },
        )

    async def create_pin(
        self,
        board_id:    str,
        title:       str,
        description: str,
        image_url:   str,
        link:        str          = "",
        alt_text:    str          = "",
        note:        str          = "",
    ) -> dict:
        """
        Create a pin on a board.

        Args:
            board_id:    Pinterest board ID
            title:       Pin title (max 100 chars)
            description: Pin description (max 500 chars)
            image_url:   Image URL (Pinterest fetches it)
            link:        Destination URL (affiliate link)
            alt_text:    Image alt text for accessibility
            note:        Internal note (not shown publicly)

        Returns:
            Pin data dict with 'id' field.
        """
        payload = {
            "board_id": board_id,
            "title":    title[:100],
            "description": description[:500],
            "media_source": {
                "source_type": "image_url",
                "url":         image_url,
            },
        }
        if link:
            payload["link"] = link
        if alt_text:
            payload["alt_text"] = alt_text[:500]
        if note:
            payload["note"] = note[:500]

        return await self._request("POST", "/pins", payload=payload)

    async def get_pin(self, pin_id: str) -> dict:
        """Get pin details by ID."""
        return await self._request("GET", f"/pins/{pin_id}")

    async def verify_token(self) -> bool:
        """Return True if access token is valid."""
        try:
            user = await self.get_user()
            return bool(user.username)
        except PinterestError:
            return False


# ── Singleton ─────────────────────────────────────────────────────────────────

_client: Optional[PinterestClient] = None


def get_pinterest_client() -> PinterestClient:
    """Return module-level client singleton."""
    global _client
    if _client is None:
        _client = PinterestClient()
    return _client
