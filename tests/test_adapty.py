from datetime import datetime, timedelta, timezone

import pytest
import requests

from resolve import adapty
from resolve.adapty import AdaptyError, Client, compensation_expiry, is_active

PROFILE_URL = adapty.BASE_URL + adapty.PROFILE_PATH
GRANT_URL = adapty.BASE_URL + adapty.GRANT_PATH
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def profile(access_levels=()):
    return {
        "data": {
            "app_id": "app",
            "profile_id": "pid",
            "customer_user_id": "lumen_1_00001",
            "total_revenue_usd": 0,
            "segment_hash": "x",
            "timestamp": 1,
            "custom_attributes": [],
            "access_levels": list(access_levels),
            "subscriptions": [],
            "non_subscriptions": [],
        }
    }


def test_get_profile_sends_api_key_and_customer_id(http):
    http.get(PROFILE_URL, json=profile())
    data = Client("sk").get_profile("lumen_1_00001")
    assert data["profile_id"] == "pid"
    sent = http.calls[0].request.headers
    assert sent["Authorization"] == "Api-Key sk"
    assert sent["adapty-customer-user-id"] == "lumen_1_00001"


def test_4xx_becomes_adapty_error_with_code(http):
    http.get(
        PROFILE_URL,
        status=404,
        json={"errors": [{"source": None, "errors": ["Profile not found"]}], "error_code": "profile_does_not_exist", "status_code": 404},
    )
    with pytest.raises(AdaptyError) as exc:
        Client("sk").get_profile("nobody")
    assert exc.value.status == 404
    assert exc.value.code == "profile_does_not_exist"
    assert "Profile not found" in str(exc.value)


def test_timeout_becomes_adapty_error(http):
    http.post(GRANT_URL, body=requests.exceptions.ConnectTimeout("slow"))
    with pytest.raises(AdaptyError) as exc:
        Client("sk").grant_access_level("lumen_1_00001", "premium", NOW)
    assert exc.value.status is None
    assert exc.value.code == "timeout"


def test_grant_sends_access_level_and_utc_expiry(http):
    http.post(GRANT_URL, json=profile([{"access_level_id": "premium", "expires_at": "2026-11-14T12:00:00+00:00"}]))
    data = Client("sk").grant_access_level("lumen_1_00001", "premium", NOW + timedelta(days=7))
    assert http.calls[0].request.body == b'{"access_level_id": "premium", "expires_at": "2026-11-14T12:00:00Z"}'
    assert data["access_levels"][0]["expires_at"] == "2026-11-14T12:00:00+00:00"


def test_compensation_starts_now_for_free_or_expired_user():
    free = profile()["data"]
    expired = profile([{"access_level_id": "premium", "expires_at": "2026-10-01T00:00:00Z"}])["data"]
    assert compensation_expiry(free, "premium", 7, NOW) == NOW + timedelta(days=7)
    assert compensation_expiry(expired, "premium", 7, NOW) == NOW + timedelta(days=7)


def test_compensation_extends_active_access_level():
    active = profile([{"access_level_id": "premium", "expires_at": "2026-11-09T10:37:31Z"}])["data"]
    assert compensation_expiry(active, "premium", 7, NOW) == datetime(2026, 11, 16, 10, 37, 31, tzinfo=timezone.utc)
    assert is_active(active["access_levels"][0], NOW)


def test_compensation_refuses_lifetime():
    lifetime = profile([{"access_level_id": "premium", "expires_at": None}])["data"]
    with pytest.raises(ValueError):
        compensation_expiry(lifetime, "premium", 7, NOW)


def test_update_profile_sends_custom_attributes(http):
    http.patch(PROFILE_URL, json=profile())
    Client("sk").update_profile("lumen_1_00001", {"last_support_ticket": "demo-1"})
    assert http.calls[0].request.body == b'{"custom_attributes": [{"key": "last_support_ticket", "value": "demo-1"}]}'
