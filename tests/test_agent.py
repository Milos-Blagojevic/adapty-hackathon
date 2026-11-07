from datetime import datetime, timezone

import pydantic
import pytest

from resolve import agent
from resolve.agent import MAX_CALLS, AgentError, Proposal, profile_summary, ticket_message, validate
from tests.fakes import ACTIVE_WEEKLY, LIFETIME, FakeAnthropic, profile, proposal, ticket_row, validation_error

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("field,value", [("category", "angry"), ("action", "refund"), ("confidence", 1.5), ("evidence", [])])
def test_output_outside_the_enums_is_rejected(field, value):
    with pytest.raises(pydantic.ValidationError):
        proposal(**{field: value})


def test_grant_rejected_for_lifetime_user():
    assert validate(proposal(action="grant_access"), profile(access_levels=[LIFETIME])) is not None
    assert validate(proposal(action="explain"), profile(access_levels=[LIFETIME])) is None
    assert validate(proposal(action="grant_access"), profile(access_levels=[ACTIVE_WEEKLY])) is None


def test_profile_summary_shows_compensation_effect():
    active = profile_summary(profile(access_levels=[ACTIVE_WEEKLY]), NOW)
    assert active["access_levels"][0]["active"] is True
    assert active["compensation"] == {"access_level": "premium", "days": 7, "available": True, "would_expire_at": "2026-11-20T05:08:30Z", "reason_unavailable": None}
    lifetime = profile_summary(profile(access_levels=[LIFETIME]), NOW)
    assert lifetime["compensation"]["available"] is False
    assert "seed_source" not in profile_summary(profile(attrs={"seed_source": "x", "goal": "family"}), NOW)["custom_attributes"]


def test_ticket_message_carries_text_and_profile_but_not_the_generator_rule():
    message = ticket_message(ticket_row(), profile(access_levels=[ACTIVE_WEEKLY]), NOW)
    assert "got charged €6.99" in message and "lumen_weekly" in message
    assert "charged_after_trial" not in message


def test_invalid_output_gets_one_retry_with_the_reason():
    client = FakeAnthropic([proposal(action="grant_access"), proposal(action="explain")])
    result, calls = agent.propose(client, "m", ticket_row(), profile(access_levels=[LIFETIME]), calls_used=0, now=NOW)
    assert (result.action, calls) == ("explain", 2)
    assert "rejected" in client.calls[1]["messages"][0]["content"]
    assert client.calls[0]["messages"][0]["content"] == ticket_message(ticket_row(), profile(access_levels=[LIFETIME]), NOW)


def test_schema_errors_count_against_the_cap():
    client = FakeAnthropic([validation_error(), validation_error()])
    with pytest.raises(AgentError) as exc:
        agent.propose(client, "m", ticket_row(), profile(), calls_used=0, now=NOW)
    assert exc.value.calls == 2
    client = FakeAnthropic([validation_error()])
    with pytest.raises(AgentError) as exc:
        agent.propose(client, "m", ticket_row(), profile(), calls_used=2, now=NOW)
    assert exc.value.calls == 1
    client = FakeAnthropic([])
    with pytest.raises(AgentError, match="cap reached"):
        agent.propose(client, "m", ticket_row(), profile(), calls_used=MAX_CALLS, now=NOW)
    assert client.calls == []
