"""Each triage option carries its own confidence (0..1, optional so tickets triaged before it still load)."""

import pytest
from pydantic import ValidationError

from resolve.llm_schema import strict_schema
from resolve.models import TriageOption, TriageTicket
from tests.fakes_v2 import grant_dict, make_proposed
from tests.test_v2_api import build


def _option_schema(node):
    if isinstance(node, dict):
        if "properties" in node and "emoji" in node["properties"]:
            return node
        node = list(node.values())
    if isinstance(node, list):
        for v in node:
            found = _option_schema(v)
            if found:
                return found
    return None


def test_the_model_is_asked_for_each_options_confidence():
    option = _option_schema(strict_schema(TriageTicket))
    assert "confidence" in option["required"]


def test_option_confidence_reaches_the_ticket_api(tmp_path):
    env = build(tmp_path)
    data = grant_dict()
    data["options"][0]["confidence"] = 0.86
    make_proposed(env.conn, "t-001", ticket=TriageTicket.model_validate(data))
    options = env.client.get("/api/tickets/t-001").json()["triage"]["options"]
    assert options[0]["confidence"] == 0.86
    assert options[1]["confidence"] is None  # not given: null, the UI shows no ring


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_option_confidence_is_bounded(bad):
    data = grant_dict()["options"][0]
    with pytest.raises(ValidationError):
        TriageOption.model_validate({**data, "confidence": bad})
