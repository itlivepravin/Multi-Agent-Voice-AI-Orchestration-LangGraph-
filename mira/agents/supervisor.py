"""
Supervisor / router node (Ch. 6.4 of the design doc).

A cheap Tier-1 LLM call classifies the current turn against the 8 domains.
Follow-up turns with an already-active agent take a fast path that skips
full re-classification (Ch. 6, Q1) to avoid re-routing overhead/context loss.
"""
from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from mira.config import ModelTier, ROUTING_CONFIDENCE_THRESHOLD, get_chat_model
from mira.state import AgentName, MiraState

_DOMAINS: list[AgentName] = [
    "buying", "selling_obv", "insurance", "loans",
    "service_booking", "dealer_support", "support", "general",
]

_ROUTER_SYSTEM_PROMPT = f"""You are MIRA's routing classifier. Given the latest user turn and \
recent conversation, classify it into exactly one of these domains:

{', '.join(_DOMAINS)}

- buying: searching/comparing vehicles to purchase
- selling_obv: selling a vehicle, valuation questions
- insurance: quotes, renewals, claims, add-ons
- loans: financing, EMI, eligibility
- service_booking: booking/managing vehicle service appointments
- dealer_support: B2B dealer/DSA requests (inventory, leads, payouts)
- support: general account/order/complaint support
- general: greeting, small talk, or anything unclear

Respond with the domain and a confidence score from 0 to 1."""


class RouteDecision(BaseModel):
    domain: Literal[
        "buying", "selling_obv", "insurance", "loans",
        "service_booking", "dealer_support", "support", "general",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(description="one short sentence")


def _looks_like_continuation(state: MiraState, user_text: str) -> bool:
    """Cheap heuristic fast-path (Ch. 6, Q1): if there's an active agent and
    the turn is short/doesn't obviously signal a topic change, skip full
    LLM re-classification."""
    if not state.get("active_agent"):
        return False
    change_signals = ["actually", "instead", "never mind", "forget that", "different"]
    return not any(sig in user_text.lower() for sig in change_signals) and len(user_text.split()) < 25


def supervisor_node(state: MiraState) -> dict:
    user_text = _last_user_text(state)

    if state.get("active_agent") and _looks_like_continuation(state, user_text):
        return {
            "active_agent": state["active_agent"],
            "routing_confidence": 1.0,
            "routing_history": state.get("routing_history", []) + [state["active_agent"]],
            "node_visit_count": state.get("node_visit_count", 0) + 1,
        }

    llm = get_chat_model(ModelTier.TIER_1_FAST).with_structured_output(RouteDecision)
    recent = state.get("messages", [])[-6:]
    decision: RouteDecision = llm.invoke(
        [SystemMessage(content=_ROUTER_SYSTEM_PROMPT), *recent, HumanMessage(content=user_text)]
    )

    domain = decision.domain
    if decision.confidence < ROUTING_CONFIDENCE_THRESHOLD:
        domain = "general"  # ask a clarifying question rather than guessing (Ch. 6.4)

    return {
        "active_agent": domain,
        "routing_confidence": decision.confidence,
        "routing_history": state.get("routing_history", []) + [domain],
        "node_visit_count": state.get("node_visit_count", 0) + 1,
    }


def _last_user_text(state: MiraState) -> str:
    for msg in reversed(state.get("messages", [])):
        role = getattr(msg, "type", None)
        if role == "human":
            return msg.content
        if isinstance(msg, dict) and msg.get("role") == "user":
            return msg.get("content", "")
    return ""
