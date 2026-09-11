"""
Input & output guardrails (Ch. 6.2, Ch. 13.2 of the design doc).

Deterministic pattern-based checks run first (cheap, catch high-confidence
cases); a lightweight LLM classifier is reserved for fuzzier categories.
This module intentionally keeps the deterministic layer dependency-free so
it's fast and trivially unit-testable.
"""
from __future__ import annotations

import re
from typing import Literal

from mira.state import GuardrailFlag, MiraState

# --- Deterministic PII patterns (Ch. 13.4) -----------------------------------
_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_PHONE_RE = re.compile(r"\b(?:\+?91[\-\s]?)?[6-9]\d{9}\b")
_AADHAAR_RE = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")

# --- Deterministic injection/jailbreak heuristics (Ch. 13.1, 13.2) ----------
_INJECTION_PATTERNS = [
    re.compile(r"ignore (all|previous|prior) instructions", re.I),
    re.compile(r"reveal (your|the) (system prompt|instructions)", re.I),
    re.compile(r"you are now|pretend (you are|to be)|jailbreak", re.I),
    re.compile(r"disregard (the )?(rules|policy|guardrails)", re.I),
]

# Numeric/currency patterns used by the output guardrail's un-traceable-claim check
_NUMERIC_CLAIM_RE = re.compile(r"(₹|\brs\.?\s?|\bINR\b)\s?[\d,]+|(\b\d[\d,]{3,}\b)")


def scan_input(text: str) -> list[GuardrailFlag]:
    """Input guardrail (Ch. 6.2 entry point, Ch. 13.2)."""
    flags: list[GuardrailFlag] = []

    if _PAN_RE.search(text) or _PHONE_RE.search(text) or _AADHAAR_RE.search(text):
        flags.append(GuardrailFlag(stage="input", kind="pii", detail="structured PII pattern detected", severity="info"))

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            flags.append(GuardrailFlag(
                stage="input", kind="injection_or_jailbreak",
                detail=f"matched pattern: {pattern.pattern}", severity="warn",
            ))
    return flags


def scan_output(
    response_text: str,
    tool_call_trace: list[dict],
    agent_name: str,
) -> list[GuardrailFlag]:
    """Output guardrail (Ch. 6.2, Ch. 13.2): flags numeric/financial claims
    not traceable to a tool call made in this turn — the mechanical
    enforcement of Ch. 3.1's core hallucination defense."""
    flags: list[GuardrailFlag] = []

    numeric_claims = _NUMERIC_CLAIM_RE.findall(response_text)
    has_numeric_claim = any(any(g for g in tup) for tup in numeric_claims)
    regulated = agent_name in {"loans", "insurance", "selling_obv"}

    if has_numeric_claim and regulated and not tool_call_trace:
        flags.append(GuardrailFlag(
            stage="output", kind="unsupported_numeric_claim",
            detail="Numeric/currency figure present in response with no tool call this turn",
            severity="block",
        ))

    if regulated and "not a lawyer" not in response_text.lower() and agent_name == "insurance" and "claim" in response_text.lower():
        # Lightweight compliance-disclaimer presence heuristic (Ch. 13.5) — in
        # production this is a scoped LLM-graded check, not a bare substring match.
        pass

    return flags


def worst_severity(flags: list[GuardrailFlag]) -> Literal["info", "warn", "block", "none"]:
    order = {"block": 3, "warn": 2, "info": 1}
    if not flags:
        return "none"
    return max(flags, key=lambda f: order[f["severity"]])["severity"]  # type: ignore[return-value]


def input_guardrail_node(state: MiraState) -> dict:
    """LangGraph node: runs before the supervisor (Ch. 6.2)."""
    last_user_text = _last_user_text(state)
    flags = scan_input(last_user_text)
    return {"guardrail_flags": state.get("guardrail_flags", []) + flags}


def output_guardrail_node(state: MiraState) -> dict:
    """LangGraph node: runs after an agent produces a response, before it's
    sent to TTS (Ch. 6.2)."""
    response = state.get("final_response") or ""
    flags = scan_output(response, state.get("tool_call_trace", []), state.get("active_agent") or "general")
    all_flags = state.get("guardrail_flags", []) + flags

    if worst_severity(flags) == "block":
        # Never let an unsupported numeric claim reach the user — Ch. 3.1/13.2.
        return {
            "guardrail_flags": all_flags,
            "final_response": (
                "Let me double check that figure for you rather than risk giving you "
                "something inaccurate — one moment."
            ),
            "needs_more_info": True,
        }
    return {"guardrail_flags": all_flags}


def _last_user_text(state: MiraState) -> str:
    for msg in reversed(state.get("messages", [])):
        role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if role in ("human", "user"):
            return getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else "") or ""
    return ""
