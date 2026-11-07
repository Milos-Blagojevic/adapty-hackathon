"""Adapty server-side API v2: the only module that talks to api.adapty.io.

Docs: https://adapty.io/docs/ss-authorization.md and
https://adapty.io/docs/api-adapty/operations/{getProfile,grantAccessLevel}.md

Auth is `Authorization: Api-Key <secret>`; the user is addressed by the `adapty-customer-user-id`
header. Responses wrap the profile in `{"data": {...}}`; errors come as
`{"errors": [...], "error_code": "...", "status_code": N}`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

BASE_URL = "https://api.adapty.io"
PROFILE_PATH = "/api/v2/server-side-api/profile/"
GRANT_PATH = "/api/v2/server-side-api/purchase/profile/grant/access-level/"
TIMEOUT_SECONDS = 15


class AdaptyError(Exception):
    """A failed Adapty call. `status` is None for a timeout or connection error."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code

    def __str__(self) -> str:
        head = f"Adapty {self.status}" if self.status else "Adapty"
        return f"{head} {self.code or ''}: {self.args[0]}".replace("  ", " ")


class Client:
    def __init__(self, secret_key: str, base_url: str = BASE_URL, session: requests.Session | None = None):
        self._headers = {
            "Authorization": f"Api-Key {secret_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._base_url = base_url.rstrip("/")
        self._session = session or requests.Session()

    def get_profile(self, customer_user_id: str) -> dict:
        return self._request("GET", PROFILE_PATH, customer_user_id)

    def grant_access_level(
        self,
        customer_user_id: str,
        access_level_id: str,
        expires_at: datetime | None,
        starts_at: datetime | None = None,
    ) -> dict:
        """Grant `access_level_id` until `expires_at` (None = lifetime). Returns the updated profile.

        A 2xx with an unreadable body still means the grant applied: it returns `{}` rather than
        raising, so the caller never treats a successful grant as a failure (and grants twice).
        """
        body: dict = {"access_level_id": access_level_id}
        if expires_at is not None:
            body["expires_at"] = format_datetime(expires_at)
        if starts_at is not None:
            body["starts_at"] = format_datetime(starts_at)
        try:
            return self._request("POST", GRANT_PATH, customer_user_id, json=body) or {}
        except AdaptyError as exc:
            if exc.code == "bad_response":
                return {}
            raise

    def update_profile(self, customer_user_id: str, custom_attributes: dict[str, str | float]) -> dict:
        """Set custom attributes (merged by key; ≤30 chars key, ≤50 chars value). Returns the updated profile."""
        body = {"custom_attributes": [{"key": key, "value": value} for key, value in custom_attributes.items()]}
        return self._request("PATCH", PROFILE_PATH, customer_user_id, json=body)

    def _request(self, method: str, path: str, customer_user_id: str, json: dict | None = None) -> dict:
        headers = dict(self._headers, **{"adapty-customer-user-id": customer_user_id})
        try:
            response = self._session.request(
                method, self._base_url + path, headers=headers, json=json, timeout=TIMEOUT_SECONDS
            )
        except requests.Timeout:
            raise AdaptyError(f"timed out after {TIMEOUT_SECONDS} s", code="timeout") from None
        except requests.RequestException as exc:
            raise AdaptyError(f"connection failed: {exc.__class__.__name__}", code="connection") from None
        if response.status_code >= 400:
            raise _error(response)
        try:
            return response.json()["data"]
        except (ValueError, KeyError, TypeError):
            raise AdaptyError("unexpected response body", status=response.status_code, code="bad_response") from None


def _error(response: requests.Response) -> AdaptyError:
    """Turn any non-2xx into AdaptyError, whatever shape the body has (`errors: null`, a number, a string, HTML)."""
    code, message = None, None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        code = body.get("error_code") if isinstance(body.get("error_code"), str) else None
        items = body.get("errors")
        messages = []
        for item in items if isinstance(items, list) else []:
            inner = item.get("errors") if isinstance(item, dict) else None
            messages.extend(str(m) for m in (inner if isinstance(inner, list) else []))
        message = "; ".join(messages) or None
    return AdaptyError(message or response.text[:200] or response.reason or "request failed", status=response.status_code, code=code)


# --- Profile helpers (pure; used by the agent, the approve flow and the templates) ---


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def access_level(profile: dict, access_level_id: str) -> dict | None:
    for level in profile.get("access_levels", []):
        if level.get("access_level_id") == access_level_id:
            return level
    return None


def is_active(level: dict | None, now: datetime) -> bool:
    if level is None:
        return False
    expires_at = parse_datetime(level.get("expires_at"))
    return expires_at is None or expires_at > now


def compensation_expiry(profile: dict, access_level_id: str, days: int, now: datetime) -> datetime:
    """`max(now, current expiry) + days`: extends an active access level, starts now otherwise.

    Raises ValueError for a lifetime access level (no `expires_at`): nothing to extend.
    """
    level = access_level(profile, access_level_id)
    base = now
    if level is not None:
        expires_at = parse_datetime(level.get("expires_at"))
        if expires_at is None:
            raise ValueError(f"{access_level_id} is lifetime on this profile; nothing to extend")
        base = max(now, expires_at)
    return base + timedelta(days=days)
