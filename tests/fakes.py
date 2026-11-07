"""Fakes shared by the workflow, agent and web tests. No network."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pydantic

from resolve import adapty
from resolve.agent import Proposal

PROFILE_URL = adapty.BASE_URL + adapty.PROFILE_PATH
GRANT_URL = adapty.BASE_URL + adapty.GRANT_PATH


def profile(customer="lumen_443814_00549", access_levels=(), subscriptions=(), attrs=None):
    return {
        "app_id": "app",
        "profile_id": "pid-" + customer[-5:],
        "customer_user_id": customer,
        "total_revenue_usd": 15.59,
        "segment_hash": "x",
        "timestamp": 1,
        "custom_attributes": [{"key": k, "value": v} for k, v in (attrs or {"acquisition_channel": "meta_ads"}).items()],
        "access_levels": list(access_levels),
        "subscriptions": list(subscriptions),
        "non_subscriptions": [],
    }


ACTIVE_WEEKLY = {
    "access_level_id": "premium",
    "store": "play_store",
    "store_product_id": "lumen_weekly",
    "expires_at": "2026-11-13T05:08:30+00:00",
    "renewal_cancelled_at": None,
    "billing_issue_detected_at": None,
    "is_in_grace_period": False,
    "cancellation_reason": None,
}
LIFETIME = dict(ACTIVE_WEEKLY, store="app_store", store_product_id="lumen.lifetime", expires_at=None)


def proposal(**overrides) -> Proposal:
    base = {
        "category": "charged_after_trial",
        "root_cause": "The weekly trial converted on Oct 30 and the user missed the date.",
        "confidence": 0.9,
        "action": "grant_access",
        "reply": "Sorry about the surprise. I added 7 days of premium; here is how to cancel.",
        "evidence": ["subscriptions[0].offer: free_trial"],
    }
    base.update(overrides)
    return Proposal.model_validate(base)


def validation_error() -> pydantic.ValidationError:
    try:
        Proposal.model_validate({"category": "nope", "root_cause": "", "confidence": 0.5, "action": "explain", "reply": "", "evidence": ["x"]})
    except pydantic.ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


class FakeAnthropic:
    """Scripted `messages.parse`: each call pops the next output (a Proposal, an Insight or an exception)."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls: list[dict] = []
        self.messages = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outputs:
            raise AssertionError("FakeAnthropic: more calls than scripted outputs")
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(parsed_output=out, content=[SimpleNamespace(text=json.dumps(out.model_dump()))])


def tickets_file(tmp_path, tickets):
    path = tmp_path / "tickets.json"
    path.write_text(json.dumps(tickets))
    return path


def ticket_row(id="t-001", customer="lumen_443814_00549", rule="charged_after_trial", demo=False):
    return {
        "id": id,
        "customer_user_id": customer,
        "subject": "Charged after free trial",
        "body": "I started the trial and got charged €6.99.",
        "created_at": "2026-11-06T18:40:00Z",
        "demo": demo,
        "rule": rule,
        "context": {"placement": "onboarding", "flow": "onb_weekly", "ab_test": "Onboarding: annual trial vs weekly first", "country": "FR", "platform": "Android", "acquisition_channel": "meta_ads"},
    }
