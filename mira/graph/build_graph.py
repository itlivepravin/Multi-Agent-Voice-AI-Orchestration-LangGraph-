"""
Assembles the MIRA LangGraph state graph (Ch. 6.2's block diagram, as code).

    entry -> input_guardrail -> supervisor -> {specialist agents} -> needs_more_info?
        -> (yes) back to supervisor / (no) output_guardrail -> escalation_decision
        -> respond | human_handoff -> END

Every node is a stateless function reading/writing MiraState (Ch. 6.6) so
any pod can process any turn — horizontal scaling has no session affinity
requirement beyond the checkpointer.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from mira.agents import SPECIALIST_DOMAINS, make_specialist_node, supervisor_node
from mira.graph.routing import after_agent, escalation_decision, route_to_specialist
from mira.guardrails import input_guardrail_node, output_guardrail_node
from mira.memory import get_checkpointer
from mira.state import MiraState


def entry_node(state: MiraState) -> dict:
    """First node in every turn (Ch. 6.2's `entry_node`). Session/memory
    loading happens upstream of graph invocation in this scaffold (see
    `mira/state.py::new_session_state` + LangGraph's checkpointer restoring
    prior state for an existing `thread_id`) — this node's only job is
    loop-protection bookkeeping via `node_visit_count` (Ch. 19, Q79)."""
    return {"node_visit_count": state.get("node_visit_count", 0) + 1}


def human_handoff_node(state: MiraState) -> dict:
    """Ch. 6.5: structured summary + full trace pushed to CCaaS queue."""
    summary = {
        "session_id": state["session_id"],
        "domain": state.get("active_agent"),
        "known_slots": state.get("slots", {}),
        "reason": state.get("escalation_reason") or "low_confidence_or_user_request",
        "tool_calls": [t["name"] for t in state.get("tool_call_trace", [])],
    }
    # Production: push `summary` + full transcript to the CCaaS queue API here.
    return {
        "final_response": (
            "I'm connecting you with one of our specialists who can take it from here — "
            "they'll have everything we've discussed already."
        ),
        "agent_scratchpad": {**state.get("agent_scratchpad", {}), "handoff_summary": summary},
    }


def respond_node(state: MiraState) -> dict:
    """Terminal node before streaming to TTS (Ch. 4)."""
    return {}  # final_response already set by the agent/guardrail node


def escalate_decision_node(state: MiraState) -> dict:
    """Pure routing checkpoint (Ch. 6.5) — makes no state changes itself;
    exists only so `escalation_decision` (mira/graph/routing.py) has a
    distinct node to attach its conditional edges to, which keeps the
    routing decision visible as its own step in LangSmith traces rather
    than being folded silently into `output_guardrail`."""
    return {}


def build_graph():
    graph = StateGraph(MiraState)

    graph.add_node("entry", entry_node)
    graph.add_node("input_guardrail", input_guardrail_node)
    graph.add_node("supervisor", supervisor_node)

    for domain in SPECIALIST_DOMAINS:
        graph.add_node(domain, make_specialist_node(domain))

    graph.add_node("output_guardrail", output_guardrail_node)
    graph.add_node("escalate_decision", escalate_decision_node)
    graph.add_node("respond", respond_node)
    graph.add_node("human_handoff", human_handoff_node)

    graph.set_entry_point("entry")
    graph.add_edge("entry", "input_guardrail")
    graph.add_edge("input_guardrail", "supervisor")

    graph.add_conditional_edges("supervisor", route_to_specialist, {d: d for d in SPECIALIST_DOMAINS})

    for domain in SPECIALIST_DOMAINS:
        graph.add_conditional_edges(
            domain, after_agent,
            {"supervisor": "supervisor", "output_guardrail": "output_guardrail", "escalate_decision": "escalate_decision"},
        )

    graph.add_edge("output_guardrail", "escalate_decision")
    graph.add_conditional_edges(
        "escalate_decision", escalation_decision,
        {"respond": "respond", "human_handoff": "human_handoff"},
    )

    graph.add_edge("respond", END)
    graph.add_edge("human_handoff", END)

    return graph.compile(checkpointer=get_checkpointer())


mira_graph = build_graph()
