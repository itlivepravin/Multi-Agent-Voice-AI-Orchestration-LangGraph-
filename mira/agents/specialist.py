"""
Generic specialist-agent factory (Ch. 6.2, 6.4 of the design doc).

Each specialist agent is a small, bounded ReAct-style loop (reason -> call
tool -> observe -> repeat, capped at MAX_TOOL_CALLS_PER_TURN) scoped to
only its domain's tools and system prompt — never a global tool pool
(Ch. 3.4, Ch. 6, Q4). Building one parameterized factory instead of 7
near-duplicate agent files is what makes "add a 9th domain" (Ch. 6, Q4) a
config-plus-prompt change, not a new code pattern.
"""
from __future__ import annotations

import time

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from mira.config import MAX_TOOL_CALLS_PER_TURN, ModelTier, get_chat_model
from mira.memory import retrieve_context
from mira.prompts import build_system_prompt
from mira.state import MiraState, ToolCallRecord
from mira.tools import ALL_TOOLS_BY_AGENT

# Domains whose turns default to the frontier tier due to reasoning complexity
# or stakes (Ch. 5.1, Ch. 1.6) rather than the fast Tier-1 default.
_FRONTIER_DOMAINS = {"loans", "insurance"}


def make_specialist_node(agent_name: str):
    """Returns a LangGraph node function bound to `agent_name`'s tools,
    prompt layer, and model tier."""
    tools = ALL_TOOLS_BY_AGENT.get(agent_name, [])
    tools_by_name = {t.name: t for t in tools}
    tier = ModelTier.TIER_2_FRONTIER if agent_name in _FRONTIER_DOMAINS else ModelTier.TIER_1_FAST

    def node(state: MiraState) -> dict:
        llm = get_chat_model(tier)
        llm_with_tools = llm.bind_tools(tools) if tools else llm

        retrieved = retrieve_context(_last_user_text(state), domain=agent_name)
        system_prompt = build_system_prompt(agent_name, state, retrieved_context=retrieved)

        conversation = [SystemMessage(content=system_prompt), *state.get("messages", [])]
        new_tool_records: list[ToolCallRecord] = []

        for _ in range(MAX_TOOL_CALLS_PER_TURN):
            ai_msg: AIMessage = llm_with_tools.invoke(conversation)
            conversation.append(ai_msg)

            if not getattr(ai_msg, "tool_calls", None):
                return {
                    "final_response": ai_msg.content,
                    "messages": [ai_msg],
                    "tool_call_trace": state.get("tool_call_trace", []) + new_tool_records,
                    "retrieved_context": retrieved,
                    "needs_more_info": False,
                    "node_visit_count": state.get("node_visit_count", 0) + 1,
                }

            for call in ai_msg.tool_calls:
                start = time.monotonic()
                tool_fn = tools_by_name.get(call["name"])
                ok = True
                try:
                    result = tool_fn.invoke(call["args"]) if tool_fn else {"error": "unknown tool"}
                    if tool_fn is None:
                        ok = False
                except Exception as exc:  # noqa: BLE001 — Ch. 18.2 fail-toward-honesty
                    result = {"error": str(exc)}
                    ok = False

                latency_ms = (time.monotonic() - start) * 1000
                new_tool_records.append(ToolCallRecord(
                    name=call["name"], args=call["args"], result=result, ok=ok, latency_ms=latency_ms,
                ))
                conversation.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

        # Hit MAX_TOOL_CALLS_PER_TURN without a final answer (Ch. 6, Q2) —
        # bounded exit rather than spiraling, protects the latency budget.
        fallback = (
            "I need a moment to pull this together properly — let me get back to you, "
            "or I can connect you with someone who can help right now."
        )
        return {
            "final_response": fallback,
            "tool_call_trace": state.get("tool_call_trace", []) + new_tool_records,
            "retrieved_context": retrieved,
            "escalation_flag": True,
            "escalation_reason": "tool_call_budget_exceeded",
            "node_visit_count": state.get("node_visit_count", 0) + 1,
        }

    node.__name__ = f"{agent_name}_agent_node"
    return node


def _last_user_text(state: MiraState) -> str:
    for msg in reversed(state.get("messages", [])):
        if getattr(msg, "type", None) == "human":
            return msg.content
    return ""
