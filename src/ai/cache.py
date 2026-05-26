"""SQLite-backed cache for LLM-generated pane prose.

Cache key = (gw, pane_type, recommendation_hash). The hash is computed over the
canonicalised payload (sorted keys, compact separators) so identical inputs
produce identical hashes. When the deterministic engine's output changes, the
hash changes — automatic invalidation, no manual cache-bust logic.
"""
import hashlib
import json
from datetime import datetime, timezone


def recommendation_hash(payload: dict) -> str:
    """Stable 32-char hex of sorted-keys JSON of payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def get(conn, gw: int, pane_type: str, rec_hash: str) -> dict | None:
    """Return {'prose', 'model_id', 'generated_at'} or None on miss."""
    row = conn.execute(
        "SELECT prose, model_id, generated_at FROM ai_reasoning_cache "
        "WHERE gw=? AND pane_type=? AND recommendation_hash=?",
        (gw, pane_type, rec_hash),
    ).fetchone()
    if row is None:
        return None
    return {"prose": row["prose"], "model_id": row["model_id"],
            "generated_at": row["generated_at"]}


def put(conn, gw: int, pane_type: str, rec_hash: str, prose: str, model_id: str) -> None:
    """Upsert one cache row."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO ai_reasoning_cache "
        "(gw, pane_type, recommendation_hash, prose, model_id, generated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (gw, pane_type, rec_hash, prose, model_id, now),
    )
    conn.commit()
