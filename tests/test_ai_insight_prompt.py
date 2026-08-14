import json

from src.ai.insight.prompt import build_analysis_prompt


def test_prompt_contains_full_system_and_user_sections():
    digest = {"player": {"web_name": "Haaland", "price": 15.0}, "data_limits": ["x"]}
    p = build_analysis_prompt(digest)
    assert "## system" in p and "## user" in p
    assert "Output ONLY valid JSON" in p
    assert "evidence_used" in p
    assert "banned" in p.lower()


def test_prompt_embeds_digest_json_verbatim():
    digest = {"player": {"web_name": "Haaland", "price": 15.0},
              "data_limits": ["no current-season minutes yet (pre-season)"]}
    p = build_analysis_prompt(digest)
    assert '"web_name": "Haaland"' in p
    assert "no current-season minutes yet (pre-season)" in p
    # the digest must be present as JSON text for the grounding gate
    # (last ```json block — the first is the output schema)
    parsed = p.split("```json")[-1].split("```")[0]
    assert json.loads(parsed) == digest
