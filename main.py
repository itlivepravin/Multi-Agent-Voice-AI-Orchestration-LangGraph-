"""
Local text-mode conversation loop for MIRA (simulates the text/app channel
described in Ch. 19, Q75 — the graph is channel-agnostic; the voice
pipeline in Ch. 4 sits in front of this same graph in production).

Usage:
    python main.py
"""
from __future__ import annotations

import uuid

from langchain_core.messages import HumanMessage

from mira.graph import mira_graph
from mira.state import new_session_state


def run_cli():
    session_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": session_id}}
    state = new_session_state(session_id=session_id, persona="consumer", channel="text", language="en-IN")

    print("MIRA (text mode) — type 'quit' to exit.\n")
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"quit", "exit"}:
            break

        state["messages"] = state.get("messages", []) + [HumanMessage(content=user_input)]
        result = mira_graph.invoke(state, config=config)
        state = result

        print(f"MIRA: {state.get('final_response')}\n")

        if state.get("guardrail_flags"):
            print(f"  [guardrail flags this turn: {state['guardrail_flags'][-3:]}]")
        if state.get("tool_call_trace"):
            print(f"  [tools called: {[t['name'] for t in state['tool_call_trace']]}]\n")


if __name__ == "__main__":
    run_cli()
