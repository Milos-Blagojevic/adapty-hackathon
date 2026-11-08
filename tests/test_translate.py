"""Any language for the user, English for the person (docs/agents.md). No network."""

import pytest

from resolve import db, llm, triage, workflow
from resolve.llm import FakeLLM, Step
from resolve.models import Decision, OptionEdits, Rejection
from tests.fakes_v2 import mock_profile, new_db, option, paid_profile, say, services, submit, triage_dict, triage_ticket


def ru_options():
    return [
        dict(option("reply_only", reply="Годовой план стоит $49.99."), reply_en="The annual plan costs $49.99."),
        dict(option("store_instructions", reply="Откройте настройки.", kind="restore", store="app_store"), reply_en="Open settings."),
    ]


def ru_ticket(**overrides):
    base = dict(user_language="Russian", messages_en=["I want a refund."], options=ru_options())
    return triage_dict(**{**base, **overrides})


@pytest.fixture
def conn(tmp_path):
    c = new_db(tmp_path)
    db.update_ticket(c, "t-001", body="Хочу вернуть деньги за годовой план")
    return c


def run(conn, steps):
    fake = FakeLLM(steps)
    triage.run(services(conn, fake), "t-001")
    return db.get_ticket(conn, "t-001"), fake


def proposed(http, conn):
    mock_profile(http, paid_profile())
    ticket, _ = run(conn, [submit(ru_ticket())])
    assert ticket["status"] == "proposed"
    return ticket


def test_translations_are_stored_in_one_call(http, conn):
    mock_profile(http, paid_profile())
    ticket, fake = run(conn, [submit(ru_ticket())])
    assert len(fake.calls) == 1
    assert ticket["triage"]["user_language"] == "Russian"
    assert ticket["triage"]["messages_en"] == ["I want a refund."]
    assert ticket["triage"]["options"][0]["reply_en"] == "The annual plan costs $49.99."


@pytest.mark.parametrize(
    "bad",
    [
        ru_ticket(messages_en=None),
        ru_ticket(messages_en=["one", "two"]),
        ru_ticket(options=[dict(ru_options()[0], reply_en=None), ru_options()[1]]),
        ru_ticket(options=[dict(ru_options()[0], reply="Same text", reply_en="Same text"), ru_options()[1]]),
        ru_ticket(options=[dict(ru_options()[0], reply="The annual plan.", reply_en="The annual plan costs."), ru_options()[1]]),
        ru_ticket(user_language="English"),
    ],
    ids=["no-messages", "wrong-count", "no-reply-en", "copied", "reply-not-russian", "cyrillic-called-english"],
)
def test_missing_translation_gets_the_one_correction(http, conn, bad):
    mock_profile(http, paid_profile())
    ticket, fake = run(conn, [submit(bad), submit(ru_ticket())])
    assert len(fake.calls) == 2 and ticket["status"] == "proposed"


def test_second_failure_stops_with_an_error(http, conn):
    mock_profile(http, paid_profile())
    ticket, _ = run(conn, [submit(ru_ticket(messages_en=None)), submit(ru_ticket(messages_en=None))])
    assert ticket["status"] != "proposed" and "messages_en" in ticket["triage_error"]


def test_english_user_has_none_of_it(http, tmp_path):
    conn = new_db(tmp_path, name="en.sqlite")
    db.update_ticket(conn, "t-001", body="I want a refund")
    mock_profile(http, paid_profile())
    t = triage_dict(user_language="English", messages_en=["stray"], options=[dict(o, reply_en="stray") for o in ru_options()])
    t["options"][0]["reply"], t["options"][1]["reply"] = "Plain.", "Steps."
    ticket, _ = run(conn, [submit(t)])
    assert ticket["triage"]["messages_en"] is None
    assert all(o["reply_en"] is None for o in ticket["triage"]["options"])


def test_accept_unedited_sends_the_russian_and_records_both(http, conn):
    proposed(http, conn)
    fake = FakeLLM([])
    t = workflow.accept(services(conn, fake), "t-001", Decision(option=0))
    assert t["status"] == "done" and not fake.calls
    final = t["action"]["final"]
    assert final["reply"] == "Годовой план стоит $49.99."
    assert final["reply_en"] == "The annual plan costs $49.99." and final["language"] == "Russian"


def test_english_edit_is_translated_once_before_sending(http, conn):
    proposed(http, conn)
    fake = FakeLLM([say("Ваш план стоит $49.99.")])
    t = workflow.accept(services(conn, fake), "t-001", Decision(option=0, edits=OptionEdits(reply_en="Your plan costs $49.99.")))
    assert len(fake.calls) == 1 and "Russian" in fake.calls[0]["system"] and fake.calls[0]["tools"] == []
    assert t["action"]["final"]["reply"] == "Ваш план стоит $49.99."
    assert t["action"]["final"]["reply_en"] == "Your plan costs $49.99."


def test_direct_edit_is_sent_as_written_without_a_call(http, conn):
    proposed(http, conn)
    fake = FakeLLM([])
    t = workflow.accept(services(conn, fake), "t-001", Decision(option=0, edits=OptionEdits(reply="Сам написал.")))
    assert not fake.calls and t["action"]["final"]["reply"] == "Сам написал." and t["action"]["final"]["reply_en"] is None


@pytest.mark.parametrize(
    "answer",
    [llm.LLMError("down"), say(""), say("Your plan costs $49.99."), say("Ваш план стоит дорого."), Step(text="Ваш", stop="max_tokens")],
    ids=["model-error", "empty", "unchanged", "lost-number", "cut-off"],
)
def test_failed_translation_sends_nothing(http, conn, answer):
    proposed(http, conn)
    with pytest.raises(workflow.TranslationFailed, match="Russian"):
        workflow.accept(services(conn, FakeLLM([answer])), "t-001", Decision(option=0, edits=OptionEdits(reply_en="Your plan costs $49.99.")))
    t = db.get_ticket(conn, "t-001")
    assert t["status"] == "proposed" and t["action"] is None and t["reply_sent_at"] is None


def test_own_english_reply_is_translated_and_failure_keeps_the_ticket_open(http, conn):
    proposed(http, conn)
    with pytest.raises(workflow.TranslationFailed):
        workflow.reject(services(conn, FakeLLM([llm.LLMError("down")])), "t-001", Rejection(reply_en="We are on it."))
    assert db.get_ticket(conn, "t-001")["status"] == "proposed"
    t = workflow.reject(services(conn, FakeLLM([say("Мы этим занимаемся.")])), "t-001", Rejection(reply_en="We are on it."))
    assert t["status"] == "done"
    assert t["decision"]["reply"] == "Мы этим занимаемся." and t["decision"]["reply_en"] == "We are on it." and t["decision"]["language"] == "Russian"


def test_chat_gets_only_the_user_language(http, tmp_path):
    conn = new_db(tmp_path, name="chat.sqlite")
    db.ensure_conversation(conn, "s-1", "lumen_443814_00549")
    db.append_message(conn, "s-1", "user", "Хочу вернуть деньги")
    tid = db.insert_feedback(conn, "x", source="chat_handoff", customer_user_id="lumen_443814_00549", session_id="s-1")
    mock_profile(http, paid_profile())
    triage.run(services(conn, FakeLLM([submit(ru_ticket())])), tid)
    workflow.accept(services(conn, FakeLLM([say("Ваш план стоит $49.99.")])), tid, Decision(option=0, edits=OptionEdits(reply_en="Your plan costs $49.99.")))
    support = [m["content"] for m in db.list_messages(conn, "s-1") if m["role"] == "support"]
    assert support == ["Ваш план стоит $49.99."]


def test_english_ticket_sends_reply_en_as_is_without_a_call(http, tmp_path):
    conn = new_db(tmp_path, name="en2.sqlite")
    mock_profile(http, paid_profile())
    triage.run(services(conn, FakeLLM([submit(triage_ticket())])), "t-001")
    fake = FakeLLM([])
    t = workflow.accept(services(conn, fake), "t-001", Decision(option=0, edits=OptionEdits(reply_en="Hello.")))
    assert not fake.calls and t["action"]["final"]["reply"] == "Hello." and "language" not in t["action"]["final"]
