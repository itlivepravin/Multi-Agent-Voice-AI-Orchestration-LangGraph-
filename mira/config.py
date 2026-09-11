"""
Central configuration for MIRA.

Implements the tiered-model-routing strategy from Chapter 5 of the design
document: each "tier" maps to a concrete chat model, and each agent/node
declares which tier it needs rather than hard-coding a model name. This is
what makes model selection swappable (Ch. 5.2's multi-vendor abstraction)
and evaluable per-tier in LangSmith (Ch. 16).
"""
from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

load_dotenv()


class ModelTier(str, Enum):
    TIER_0_CLASSIFIER = "tier_0_classifier"   # cheapest/fastest — fast-path intent match
    TIER_1_FAST = "tier_1_fast"               # routing, slot-filling, simple FAQ
    TIER_2_FRONTIER = "tier_2_frontier"       # multi-step domain reasoning
    TIER_3_EXTENDED = "tier_3_extended"       # escalation judgment, high-stakes explanation


# Primary + failover model per tier (Ch. 5.2 / Ch. 18, Q1: provider failover).
_MODEL_MAP: dict[ModelTier, dict[str, str]] = {
    ModelTier.TIER_0_CLASSIFIER: {"primary": "claude-haiku-4-5-20251001", "failover": "gpt-4o-mini"},
    ModelTier.TIER_1_FAST: {"primary": "claude-haiku-4-5-20251001", "failover": "gpt-4o-mini"},
    ModelTier.TIER_2_FRONTIER: {"primary": "claude-sonnet-5", "failover": "gpt-4o"},
    ModelTier.TIER_3_EXTENDED: {"primary": "claude-opus-5", "failover": "gpt-4o"},
}

DEFAULT_TEMPERATURE = 0.2   # low temperature: MIRA prioritizes consistency over creativity (Ch. 9)
MAX_TOOL_CALLS_PER_TURN = 4  # Ch. 6, Q2 — hard bound to protect the latency budget (Ch. 2.2.1)
MAX_GRAPH_NODE_VISITS = 12   # Ch. 19, Q79 — prevents supervisor/agent routing loops
ROUTING_CONFIDENCE_THRESHOLD = 0.55  # below this, supervisor asks a clarifying question (Ch. 6.4)
ESCALATION_FAILED_TURN_THRESHOLD = 3  # FR-6 / Ch. 6.5


@lru_cache(maxsize=None)
def get_chat_model(tier: ModelTier, use_failover: bool = False):
    """Return a bound chat model for the given tier.

    Cached per (tier, use_failover) so repeated node invocations reuse the
    same client/connection rather than re-instantiating per call.
    """
    spec = _MODEL_MAP[tier]
    model_name = spec["failover"] if use_failover else spec["primary"]

    if model_name.startswith("claude"):
        return ChatAnthropic(
            model=model_name,
            temperature=DEFAULT_TEMPERATURE,
            max_tokens=1024,
            timeout=8.0,   # bounded — Ch. 2.2.1 latency budget
        )
    return ChatOpenAI(
        model=model_name,
        temperature=DEFAULT_TEMPERATURE,
        max_tokens=1024,
        timeout=8.0,
    )


REGULATED_DOMAINS = {"loans", "insurance"}  # Ch. 1.6 — lower escalation threshold, tool-forcing is mandatory

SUPPORTED_LANGUAGES = ["en-IN", "hi-IN", "hi-en-mixed"]  # Ch. 4.4

ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
