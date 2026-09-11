# MIRA — LangGraph Orchestration Scaffold

Working reference implementation of the multi-agent orchestration layer
described in the *MIRA Voice AI Assistant — Architecture Design Document*
(Chapters 3, 5, 6, 7, 9, 10, 11, 12, 13, and 16 in particular). This is the
**orchestration core** — the voice pipeline (ASR/TTS, Ch. 4), production MCP
servers (Ch. 11.2), and real Droom backend integrations are outside this
scaffold's scope and are stubbed with deterministic mocks so the graph is
runnable end-to-end without live infrastructure.

## What's here

```
mira/
  config.py            Tiered model routing (Ch. 5.1) + runtime constants
  state.py              Shared LangGraph state schema (Ch. 6.3)
  prompts.py             Layered system-prompt assembly (Ch. 9.1)
  guardrails.py           Input/output guardrails (Ch. 6.2, 13.2)
  memory.py               Checkpointer + RAG retrieval stubs (Ch. 7, 10)
  agents/
    supervisor.py          Router node (Ch. 6.4)
    specialist.py           Bounded ReAct specialist-agent factory (Ch. 6.2)
  tools/
    schemas.py               Strict Pydantic I/O per domain tool (Ch. 11.3)
    mcp_client.py              MCP client wrapper + mock fallback (Ch. 11.2)
    domain_tools.py             @tool-decorated functions (OBV, loans, insurance, ...)
  graph/
    routing.py                   Conditional-edge functions (Ch. 6.2)
    build_graph.py                 Assembles the compiled LangGraph (Ch. 6.2 diagram)

evals/
  dataset.py              LangSmith eval dataset incl. red-team cases (Ch. 16.5)
  evaluators.py             Deterministic + LLM-as-judge evaluators (Ch. 16.2)
  run_eval.py                 CI gate entry point (Ch. 14.5)

k8s/                    Illustrative manifests (Ch. 14.2/14.3, Ch. 15.2)
tests/                   Smoke tests (offline-runnable, no live LLM needed)
main.py                 Local text-mode conversation loop
```

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY at minimum for full graph runs

# Offline smoke tests (no API keys needed — tools run in MIRA_MCP_MOCK mode)
pytest -q

# Interactive local run (requires ANTHROPIC_API_KEY)
python main.py

# Run the LangSmith evaluation suite (requires LANGCHAIN_API_KEY + ANTHROPIC_API_KEY)
python -m evals.run_eval
```

## Design principle this code enforces

> The LLM converses; it does not compute business truth.

Concretely: every numeric/policy fact (`GetOBVValuationOutput`,
`LoanEligibilityOutput`, `GetInsuranceQuoteOutput`, ...) can only enter a
response through a validated `@tool` call (`mira/tools/domain_tools.py`).
The output guardrail (`mira/guardrails.py::scan_output`) mechanically
checks for numeric/currency claims in a response that aren't backed by a
tool call in that turn's `tool_call_trace`, and blocks/regenerates if so.
This is tested directly in `tests/test_graph_smoke.py` and exercised
adversarially in `evals/dataset.py`'s red-team examples.

## Swapping mocks for production infrastructure

| Component | Scaffold | Production swap |
|---|---|---|
| Checkpointer | `MemorySaver` (in-process) | `RedisSaver` + Postgres durable flush (Ch. 6.3, `mira/memory.py`) |
| RAG retrieval | In-memory keyword match | Pinecone hybrid dense+sparse + re-ranker (Ch. 7, 8) |
| MCP tool calls | Deterministic mock functions | Real MCP servers calling Droom core systems (Ch. 11.2, `MIRA_MCP_MOCK=false`) |
| Voice I/O | N/A (text CLI) | Deepgram ASR + ElevenLabs TTS pipeline in front of this graph (Ch. 4) |

## Evaluation gates (Ch. 16.5)

`evals/run_eval.py` enforces minimum thresholds per evaluator before a
change is considered shippable — most notably `no_unsupported_numeric_claim`
at a **hard 100% threshold**, reflecting Chapter 3.1/13.2's zero-tolerance
policy on unsourced financial figures.
