"""Fixed Level 2 replies follow the user's language, in code, with no model call."""

import pytest

from resolve import frontline
from resolve.llm import FakeLLM
from tests.fakes_v2 import CUID, PROFILE_URL, new_db, services


@pytest.fixture
def conn(tmp_path):
    return new_db(tmp_path, seed=False)


def test_every_fixed_reply_has_a_russian_text():
    for reply in (frontline.HANDED_OFF_REPLY, frontline.TOO_LONG_REPLY, frontline.FALLBACK_REPLY, frontline.AUTO_HANDOFF_REPLY, frontline.ACCOUNT_DOWN_REPLY):
        assert any("а" <= c <= "я" for c in frontline.FIXED_REPLIES_RU[reply])


def test_language_is_read_from_all_the_user_messages():
    assert frontline.writes_russian(["Верните деньги за подписку"])
    assert frontline.writes_russian(["Верните деньги за подписку", "iPhone 13"])
    assert not frontline.writes_russian(["Please refund me"])
    assert not frontline.writes_russian(["Kako da otkažem pretplatu?"])
    assert not frontline.writes_russian(["Хочу повернути гроші, її не використовую"])
    assert not frontline.writes_russian(["Како да откажем, њу не користим"])
    assert not frontline.writes_russian(["123 ???"])


def test_account_down_answers_in_the_users_language(http, conn):
    http.get(PROFILE_URL, status=500, json={"errors": [{"detail": "boom"}]})
    fake = FakeLLM([])
    ru = frontline.chat(services(conn, fake), CUID, "s-ru", "Верните деньги за подписку")
    en = frontline.chat(services(conn, fake), CUID, "s-en", "Please refund me")
    assert ru.reply == frontline.FIXED_REPLIES_RU[frontline.ACCOUNT_DOWN_REPLY] and ru.handed_off
    assert en.reply == frontline.ACCOUNT_DOWN_REPLY and en.handed_off
    assert fake.calls == []
