"""Provider choice: auto from the keys in .env, and the switch on the Agent settings page."""

from fastapi.testclient import TestClient

from resolve import config, llm, web
from resolve.adapty import Client
from resolve.llm import FakeLLM


def _env(monkeypatch, provider="auto", openai_key="", openai_model="", anthropic_key=""):
    monkeypatch.setattr(config, "LLM_PROVIDER", provider)
    monkeypatch.setattr(config, "OPENAI_MODEL", openai_model)
    for name, value in (("OPENAI_API_KEY", openai_key), ("ANTHROPIC_API_KEY", anthropic_key), ("OPENAI_MODEL", openai_model)):
        if value:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)


def test_auto_picks_anthropic_when_only_its_key_is_set(monkeypatch):
    _env(monkeypatch, anthropic_key="sk-ant-x")
    assert llm.resolve_provider() == "anthropic"
    assert llm.resolve_provider("env") == "anthropic"


def test_auto_prefers_openai_when_key_and_model_are_set(monkeypatch):
    _env(monkeypatch, openai_key="sk-x", openai_model="deepseek-flash", anthropic_key="sk-ant-x")
    assert llm.resolve_provider() == "openai"
    assert llm.make_llm().provider == "openai"


def test_explicit_choice_wins(monkeypatch):
    _env(monkeypatch, openai_key="sk-x", openai_model="m")
    assert llm.resolve_provider("anthropic") == "anthropic"


def test_openai_options_from_env(monkeypatch):
    _env(monkeypatch, provider="openai", openai_key="sk-x", openai_model="deepseek-flash")
    monkeypatch.setattr(config, "OPENAI_EXTRA_BODY", '{"thinking": {"type": "disabled"}}')
    monkeypatch.setattr(config, "OPENAI_STRICT_TOOLS", False)
    monkeypatch.setattr(config, "OPENAI_MAX_TOKENS", 1234)
    client = llm.make_llm()
    assert (client.extra_body, client.strict, client.max_tokens) == ({"thinking": {"type": "disabled"}}, False, 1234)


def _app(tmp_path):
    return TestClient(web.create_app(tmp_path / "s.sqlite", demo=False, adapty_client=Client("sk"), llm_client=FakeLLM([]), seed_file=tmp_path / "none.json"))


def test_switch_to_a_provider_without_keys_is_422_and_not_saved(tmp_path, monkeypatch):
    _env(monkeypatch)
    c = _app(tmp_path)
    cfg = c.get("/api/config").json()
    r = c.put("/api/config", json=dict(cfg, llm_provider="anthropic"))
    assert r.status_code == 422
    assert c.get("/api/config").json()["llm_provider"] == "env"
    assert c.get("/api/llm").json()["active"] == "fake"


def test_switch_rebuilds_the_client(tmp_path, monkeypatch):
    _env(monkeypatch, openai_key="sk-x", openai_model="deepseek-flash")
    c = _app(tmp_path)
    cfg = c.get("/api/config").json()
    assert c.put("/api/config", json=dict(cfg, llm_provider="openai")).status_code == 200
    info = c.get("/api/llm").json()
    assert (info["selected"], info["active"], info["model"]) == ("openai", "openai", "deepseek-flash")
    assert info["available"] == {"openai": True, "anthropic": False}
