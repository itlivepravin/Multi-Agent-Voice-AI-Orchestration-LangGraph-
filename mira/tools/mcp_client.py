"""
Thin MCP client wrapper (Ch. 11.2 of the design doc).

In production this speaks the Model Context Protocol to the per-domain MCP
servers deployed in the `mcp-servers` namespace (Ch. 14.2), which in turn
call Droom's real core systems. For this scaffold — since we don't have
Droom's actual backend to call — each domain client falls back to a
deterministic MOCK_MODE implementation so the graph is runnable and
evaluable end-to-end without live infrastructure.

Swapping MOCK_MODE off is the only change needed to point this at real MCP
servers; agent/tool-calling code above this layer never changes.
"""
from __future__ import annotations

import hashlib
import os
import random
import time
import uuid
from typing import Any, Callable, TypeVar

from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

MOCK_MODE = os.getenv("MIRA_MCP_MOCK", "true").lower() == "true"

InT = TypeVar("InT", bound=BaseModel)
OutT = TypeVar("OutT", bound=BaseModel)


class ToolExecutionError(Exception):
    """Raised when a tool call fails after retries — Ch. 11.4, Ch. 18.1's
    'Droom core system outage' row. Callers must handle this by giving an
    honest fallback response, never by substituting an LLM-guessed answer
    (Ch. 18.2's 'fail toward honesty' principle)."""


def _stable_seed(*parts: Any) -> random.Random:
    """Deterministic pseudo-randomness so mock responses are stable for a
    given input — makes the LangSmith eval suite (Ch. 16) reproducible."""
    key = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(key[:16], 16))


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.1, max=1))
def call_mcp_server(
    server_url: str,
    method: str,
    input_model: InT,
    output_model_cls: type[OutT],
    mock_fn: Callable[[InT], OutT],
    timeout_s: float = 3.0,
) -> OutT:
    """Call an MCP server tool, or the deterministic mock if MOCK_MODE.

    Retries once on transient failure (Ch. 11.4). Bounded timeout protects
    the per-turn latency budget (Ch. 2.2.1, Ch. 6, Q2).
    """
    start = time.monotonic()
    try:
        if MOCK_MODE:
            result = mock_fn(input_model)
        else:
            import httpx  # local import: optional dep in mock-only dev envs

            with httpx.Client(timeout=timeout_s) as client:
                resp = client.post(
                    f"{server_url}/tools/{method}",
                    json=input_model.model_dump(),
                )
                resp.raise_for_status()
                result = output_model_cls.model_validate(resp.json())
        return result
    except Exception as exc:  # noqa: BLE001 — deliberately broad, re-raised as domain error
        raise ToolExecutionError(f"MCP call {method} failed: {exc}") from exc
    finally:
        _ = (time.monotonic() - start) * 1000  # latency captured by caller via tool_call_trace


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
