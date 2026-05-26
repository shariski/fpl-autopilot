from src.ai import provider as prv


def test_stub_provider_returns_fixed_response():
    p = prv.StubProvider("hello world")
    assert p.generate("anything", max_tokens=10, temperature=0.0) == "hello world"


def test_stub_provider_default_response():
    p = prv.StubProvider()
    out = p.generate("prompt")
    assert isinstance(out, str)
    assert out
