"""
Memory tiers (Ch. 10) and RAG retrieval (Ch. 7) plumbing.

Production would back the checkpointer with Redis (hot/session, Ch. 6.3)
and flush to Postgres for durable episodic storage (Ch. 10.1). This
scaffold defaults to LangGraph's in-memory checkpointer so the graph is
runnable without external infra, with clear swap points for the real
implementation documented inline.
"""
from __future__ import annotations

import os

from langgraph.checkpoint.memory import MemorySaver


def get_checkpointer():
    """Ch. 6.3 / 6.6: swap this for a Redis/Postgres-backed checkpointer in
    production —

        from langgraph.checkpoint.redis import RedisSaver
        return RedisSaver.from_conn_string(os.environ["REDIS_URL"])

    RedisSaver gives sub-ms writes for hot sessions; a scheduled job flushes
    completed sessions to Postgres for durable, queryable episodic storage
    and the FR-8 audit trail.
    """
    return MemorySaver()


# ---------------------------------------------------------------------------
# RAG retrieval (Ch. 7) — hybrid dense+sparse search against Pinecone (Ch. 8),
# stubbed here with a tiny in-memory corpus so the graph is runnable offline.
# ---------------------------------------------------------------------------
_MOCK_CORPUS = [
    {"id": "faq_zero_dep", "domain": "insurance", "text": "Zero depreciation cover means the "
     "insurer pays the full claim amount without deducting for wear-and-tear or depreciation on "
     "replaced parts, for an additional premium."},
    {"id": "faq_ncb", "domain": "insurance", "text": "No Claim Bonus (NCB) is a discount on your "
     "premium for each claim-free year, ranging from 20% to 50%. NCB protect add-on preserves "
     "this discount even after one claim."},
    {"id": "faq_emi_default", "domain": "loans", "text": "Missing an EMI payment typically incurs "
     "a late fee and may affect your credit score if it crosses 30 days overdue; contact the "
     "lender proactively if you expect to miss a payment."},
    {"id": "faq_obv_method", "domain": "vehicle_specs", "text": "Droom's Orange Book Value is "
     "computed from recent comparable transactions in your city, standard depreciation curves "
     "for the make/model, and adjustments for mileage and condition."},
]


def retrieve_context(query: str, domain: str | None = None, top_k: int = 4) -> list[dict]:
    """Simplified hybrid-search stand-in (Ch. 7.2/7.3). Production
    implementation performs dense (embedding) + sparse (BM25) retrieval
    against the domain-partitioned Pinecone index (Ch. 8.2), fused via
    reciprocal rank fusion, then re-ranked with a cross-encoder (Ch. 7.4).
    Here we do simple keyword overlap so the pipeline is exercisable
    end-to-end without a live vector DB."""
    query_terms = set(query.lower().split())
    scored = []
    for doc in _MOCK_CORPUS:
        if domain and doc["domain"] != domain:
            continue
        overlap = len(query_terms & set(doc["text"].lower().split()))
        if overlap > 0:
            scored.append((overlap, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [doc for _, doc in scored[:top_k]]


# ---------------------------------------------------------------------------
# Episodic memory (Ch. 10.4) — async, off-critical-path summarization stub.
# ---------------------------------------------------------------------------
def summarize_session_to_episode(state) -> dict:
    """Constrained-extraction summarization (Ch. 10.4): builds a structured
    episode from already-structured state (slots, tool_call_trace) rather
    than free-form narrative summarization, to reduce fabrication risk."""
    return {
        "session_id": state.get("session_id"),
        "domain": state.get("active_agent"),
        "key_facts": state.get("slots", {}),
        "outcome": "escalated" if state.get("escalation_flag") else "resolved",
        "tool_calls": [t["name"] for t in state.get("tool_call_trace", [])],
    }
