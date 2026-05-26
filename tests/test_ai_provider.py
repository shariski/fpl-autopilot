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
