"""
Conditional-edge functions for the MIRA graph (Ch. 6.2's block diagram,
translated into LangGraph edge logic).
"""
from __future__ import annotations

from mira.config import ESCALATION_FAILED_TURN_THRESHOLD, MAX_GRAPH_NODE_VISITS
from mira.state import MiraState


def route_to_specialist(state: MiraState) -> str:
    """Supervisor -> specialist agent (Ch. 6.2 diagram)."""
    return state.get("active_agent") or "general"


def after_agent(state: MiraState) -> str:
    """Ch. 6.2: needs_more_info? -> loop back to supervisor, else -> output guardrail.
    Also enforces the hard node-visit bound (Ch. 19, Q79 loop protection)."""
    if state.get("node_visit_count", 0) >= MAX_GRAPH_NODE_VISITS:
        return "escalate_decision"  # force out of any pathological loop
    if state.get("needs_more_info"):
        return "supervisor"
    return "output_guardrail"


def escalation_decision(state: MiraState) -> str:
    """Ch. 6.5: escalate to human when confidence is low, user asked, or
    repeated-failure threshold is hit."""
    if state.get("escalation_flag"):
        return "human_handoff"
    if state.get("failed_turn_count", 0) >= ESCALATION_FAILED_TURN_THRESHOLD:
        return "human_handoff"
    if state.get("routing_confidence", 1.0) < 0.3:
        return "human_handoff"
    return "respond"
