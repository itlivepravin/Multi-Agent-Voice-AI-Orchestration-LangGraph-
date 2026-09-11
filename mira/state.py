"""
Shared graph state — the "single source of conversational truth" described
in Chapter 6.3 of the design document. Every LangGraph node reads/writes a
slice of this TypedDict; the whole object is what gets checkpointed after
each node (Ch. 6.3, Ch. 6.6).
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, TypedDict

from langgraph.graph.message import add_messages


class ToolCallRecord(TypedDict):
    name: str
    args: dict
    result: dict
    ok: bool
    latency_ms: float


class GuardrailFlag(TypedDict):
    stage: Literal["input", "output"]
    kind: str            # e.g. "pii", "injection", "jailbreak", "unsupported_numeric_claim"
    detail: str
    severity: Literal["info", "warn", "block"]


AgentName = Literal[
    "buying", "selling_obv", "insurance", "loans",
    "service_booking", "dealer_support", "support", "general",
]


class MiraState(TypedDict):
    # --- session identity (Ch. 6.3) ---
    session_id: str
    user_id: Optional[str]
    persona: Literal["consumer", "dealer", "dsa", "guest"]
    channel: Literal["app", "ivr", "whatsapp", "text"]
    language: str

    # --- conversation ---
    messages: Annotated[list, add_messages]   # rolling turn history (curated per Ch. 10.2)
    slots: dict                               # extracted structured fields (Ch. 6.3)
    active_agent: Optional[AgentName]
    routing_history: list[AgentName]          # Ch. 19, Q79 — loop detection

    # --- per-turn working memory ---
    agent_scratchpad: dict
    tool_call_trace: list[ToolCallRecord]     # Ch. 6.3, FR-8 audit trail
    retrieved_context: list[dict]             # RAG chunks injected this turn (Ch. 7)

    # --- decisioning ---
    routing_confidence: float
    needs_more_info: bool
    escalation_flag: bool
    escalation_reason: Optional[str]
    failed_turn_count: int
    guardrail_flags: list[GuardrailFlag]

    # --- output ---
    final_response: Optional[str]
    node_visit_count: int                     # Ch. 19, Q79 — hard loop bound


def new_session_state(
    session_id: str,
    persona: str = "consumer",
    channel: str = "text",
    language: str = "en-IN",
    user_id: str | None = None,
) -> MiraState:
    """Factory for a fresh session — mirrors the `entry_node` in Ch. 6.2."""
    return MiraState(
        session_id=session_id,
        user_id=user_id,
        persona=persona,       # type: ignore[typeddict-item]
        channel=channel,       # type: ignore[typeddict-item]
        language=language,
        messages=[],
        slots={},
        active_agent=None,
        routing_history=[],
        agent_scratchpad={},
        tool_call_trace=[],
        retrieved_context=[],
        routing_confidence=0.0,
        needs_more_info=False,
        escalation_flag=False,
        escalation_reason=None,
        failed_turn_count=0,
        guardrail_flags=[],
        final_response=None,
        node_visit_count=0,
    )
