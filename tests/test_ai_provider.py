from src.ai import provider as prv


def test_stub_provider_returns_fixed_response():
    p = prv.StubProvider("hello world")
    assert p.generate("anything", max_tokens=10, temperature=0.0) == "hello world"


def test_stub_provider_default_response():
    p = prv.StubProvider()
    out = p.generate("prompt")
    assert isinstance(out, str)
    assert out


class _FakeResponse:
    def __init__(self, status_code, json_body):
        self.status_code = status_code
        self._body = json_body

    def json(self):
        return self._body


class _FakeSession:
    def __init__(self, response=None, raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def test_ollama_provider_posts_to_generate_endpoint():
    session = _FakeSession(_FakeResponse(200, {"response": "  Haaland.  "}))
    p = prv.OllamaProvider("http://localhost:11434", "qwen2.5:7b-instruct-q4_K_M",
                           timeout_seconds=15, session=session)
    out = p.generate("hello", max_tokens=128, temperature=0.3)
    assert out == "Haaland."          # stripped
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == "http://localhost:11434/api/generate"
    assert call["timeout"] == 15
    assert call["json"]["model"] == "qwen2.5:7b-instruct-q4_K_M"
    assert call["json"]["prompt"] == "hello"
    assert call["json"]["stream"] is False
    assert call["json"]["options"]["num_predict"] == 128
    assert call["json"]["options"]["temperature"] == 0.3


def test_ollama_provider_raises_on_non_200():
    import pytest
    session = _FakeSession(_FakeResponse(500, {}))
    p = prv.OllamaProvider("http://localhost:11434", "m", timeout_seconds=15, session=session)
    with pytest.raises(prv.OllamaError):
        p.generate("hello")


def test_ollama_provider_raises_on_network_error():
    import pytest
    import requests
    session = _FakeSession(raise_exc=requests.ConnectionError("connection refused"))
    p = prv.OllamaProvider("http://localhost:11434", "m", timeout_seconds=15, session=session)
    with pytest.raises(prv.OllamaError):
        p.generate("hello")


def test_ollama_provider_raises_on_malformed_json():
    import pytest

    class _BadJson(_FakeResponse):
        def json(self):
            raise ValueError("not json")

    session = _FakeSession(_BadJson(200, None))
    p = prv.OllamaProvider("http://localhost:11434", "m", timeout_seconds=15, session=session)
    with pytest.raises(prv.OllamaError):
        p.generate("hello")


# ---------- DeepSeekProvider (OpenAI-compatible) ----------

def _fake_completions(text="Grounded 8.0 prose", usage=None):
    from types import SimpleNamespace

    def create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=usage,
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _deepseek_provider(**kw):
    from src.ai.provider import DeepSeekProvider

    defaults = dict(model="deepseek-chat", base_url="https://api.deepseek.com/v1",
                    timeout_seconds=15)
    defaults.update(kw)
    return DeepSeekProvider("sk-test", **defaults)


def test_deepseek_generate_happy_path():
    from types import SimpleNamespace

    usage = SimpleNamespace(prompt_tokens=120, completion_tokens=40)
    client = _fake_completions(usage=usage)
    p = _deepseek_provider(_client=client)
    out = p.generate("Captain Haaland for GW1.")
    assert out == "Grounded 8.0 prose"


def test_deepseek_refuses_credential_patterns():
    import pytest
    from src.ai.provider import DeepSeekError

    p = _deepseek_provider(_client=_fake_completions())
    with pytest.raises(DeepSeekError, match="credential"):
        p.generate("pl_profile leaked here")


def test_deepseek_wraps_sdk_errors():
    import pytest
    from types import SimpleNamespace
    from src.ai.provider import DeepSeekError

    class Boom:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(
                create=lambda **kw: (_ for _ in ()).throw(RuntimeError("connection reset"))))

    p = _deepseek_provider(_client=Boom())
    with pytest.raises(DeepSeekError, match="deepseek request failed"):
        p.generate("hello")


def test_deepseek_logs_usage_only_when_conn_present(db):
    from types import SimpleNamespace
    from src.ai.provider import DeepSeekProvider

    usage = SimpleNamespace(prompt_tokens=50, completion_tokens=25)
    client = _fake_completions(usage=usage)
    p = DeepSeekProvider("sk-test", model="deepseek-chat",
                         base_url="https://api.deepseek.com/v1",
                         timeout_seconds=15, conn=db, _client=client)
    p.generate("hello")
    row = db.execute("SELECT * FROM activity_log WHERE decision_type='ai.audit'").fetchone()
    assert row is not None and row["action_taken"] == "deepseek generate"
    assert '"input_tokens": 50' in row["inputs_json"]
    assert '"output_tokens": 25' in row["inputs_json"]


def test_deepseek_skips_usage_log_without_conn():
    p = _deepseek_provider(_client=_fake_completions())
    p.generate("hello")  # no conn -> no activity_log write; no exception is the assertion


# ---------- build_provider factory ----------

def test_build_provider_deepseek(monkeypatch):
    from src.ai.provider import build_provider

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    cfg = {"ai": {"provider": "deepseek"}}
    provider = build_provider(cfg)
    assert isinstance(provider, prv.DeepSeekProvider)
    assert provider.model == "deepseek-chat"


def test_build_provider_deepseek_missing_key_raises(monkeypatch):
    import pytest
    from src.ai.provider import DeepSeekError, build_provider

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg = {"ai": {"provider": "deepseek"}}
    with pytest.raises(DeepSeekError, match="DEEPSEEK_API_KEY"):
        build_provider(cfg)


def test_build_provider_ollama():
    from src.ai.provider import build_provider

    cfg = {"ai": {"provider": "ollama", "ollama": {"host": "http://x:1", "model": "m"}}}
    provider = build_provider(cfg)
    assert isinstance(provider, prv.OllamaProvider)


def test_build_provider_none_when_disabled_or_unknown():
    from src.ai.provider import build_provider

    assert build_provider({"ai": {"provider": "none"}}) is None
    assert build_provider({"ai": {"provider": "something-else"}}) is None
