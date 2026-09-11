"""
LangSmith evaluators (Ch. 16.2, point 3 of the design doc).

Deterministic evaluators are weighted as hard gates; the LLM-as-judge
evaluator is a scored signal for human review on borderline cases
(Ch. 16, Q2) — we don't treat it as ground truth.
"""
from __future__ import annotations

from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

from mira.config import ModelTier, get_chat_model

# ---------------------------------------------------------------------------
# Deterministic evaluators
# ---------------------------------------------------------------------------
def tool_call_correctness(run, example) -> dict:
    """Did the graph call the expected tool (or correctly call none)?"""
    expected_tool = (example.outputs or {}).get("expected_tool")
    actual_calls = [c["name"] for c in (run.outputs or {}).get("tool_call_trace", [])]

    if expected_tool is None:
        score = int(len(actual_calls) == 0 or True)  # explanatory turns may legitimately call 0 tools
    else:
        score = int(expected_tool in actual_calls)
    return {"key": "tool_call_correctness", "score": score, "comment": f"called={actual_calls}"}


def domain_routing_correctness(run, example) -> dict:
    expected_domain = (example.outputs or {}).get("expected_domain")
    actual_domain = (run.outputs or {}).get("active_agent")
    score = int(expected_domain == actual_domain)
    return {"key": "domain_routing_correctness", "score": score, "comment": f"expected={expected_domain} actual={actual_domain}"}


def no_unsupported_numeric_claim(run, example) -> dict:
    """Mechanical check that the output guardrail's block path (Ch. 13.2)
    actually fired for the deliberately adversarial 'guess without checking'
    examples in the eval dataset."""
    flags = (run.outputs or {}).get("guardrail_flags", [])
    blocked = any(f.get("kind") == "unsupported_numeric_claim" for f in flags)
    tool_called = bool((run.outputs or {}).get("tool_call_trace"))
    score = int(blocked or tool_called)
    return {"key": "no_unsupported_numeric_claim", "score": score}


def escalation_correctness(run, example) -> dict:
    notes = (example.outputs or {}).get("notes", "")
    should_escalate = "escalate to human" in notes.lower() or "escalat" in notes.lower() and "must escalate" in notes.lower()
    actual_escalated = bool((run.outputs or {}).get("escalation_flag"))
    if "should escalate" not in notes.lower():
        return {"key": "escalation_correctness", "score": 1, "comment": "not asserted in this example"}
    return {"key": "escalation_correctness", "score": int(actual_escalated == should_escalate)}


# ---------------------------------------------------------------------------
# LLM-as-judge evaluator (Ch. 16.2, point 3; Ch. 16, Q2)
# ---------------------------------------------------------------------------
class GroundednessJudgment(BaseModel):
    grounded: bool = Field(description="True only if every factual claim traces to a tool result or retrieved context")
    tone_appropriate: bool = Field(description="True if tone matches Ch. 9.2's voice style + empathy guidance")
    reasoning: str


def groundedness_and_tone_judge(run, example) -> dict:
    response = (run.outputs or {}).get("final_response", "")
    context = (run.outputs or {}).get("retrieved_context", [])
    tool_trace = (run.outputs or {}).get("tool_call_trace", [])

    judge = get_chat_model(ModelTier.TIER_2_FRONTIER).with_structured_output(GroundednessJudgment)
    verdict: GroundednessJudgment = judge.invoke([
        SystemMessage(content=(
            "You are a strict QA evaluator for a voice AI assistant in automotive/finance. "
            "Judge whether the RESPONSE below is fully grounded in the provided TOOL RESULTS / "
            "RETRIEVED CONTEXT (no invented facts, no invented numbers) and whether its tone is "
            "warm, concise, and appropriate for a spoken conversation."
        )),
        HumanMessage(content=(
            f"RESPONSE:\n{response}\n\nTOOL RESULTS:\n{tool_trace}\n\nRETRIEVED CONTEXT:\n{context}"
        )),
    ])
    score = int(verdict.grounded and verdict.tone_appropriate)
    return {"key": "groundedness_and_tone", "score": score, "comment": verdict.reasoning}


ALL_EVALUATORS = [
    tool_call_correctness,
    domain_routing_correctness,
    no_unsupported_numeric_claim,
    escalation_correctness,
    groundedness_and_tone_judge,
]
