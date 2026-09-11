# MIRA — Code Documentation

This is the module-by-module reference for the `mira-langgraph` scaffold.
Every file already carries a docstring pointing back to the relevant
chapter of the design document; this file connects those pieces into one
coherent picture — what calls what, in what order, and where to make a
change for a given kind of task.

## 1. Repository map

```
mira-langgraph/
├── main.py                    Local CLI entrypoint (text-mode channel)
├── requirements.txt            Python dependencies
├── .env.example                 All environment variables, documented inline
├── Dockerfile                    Production container build
├── DOCUMENTATION.md               <- you are here
├── LOCAL_SETUP.md                  Step-by-step local run guide
├── PRODUCTION_RUNBOOK.md            Post-deployment issues + autoscaling deep-dive
├── mira/                              # orchestration core
│   ├── config.py                        Model tiers, timeouts, thresholds
│   ├── state.py                          MiraState schema (the graph's data contract)
│   ├── prompts.py                         Layered system-prompt assembly
│   ├── guardrails.py                       Input/output guardrail nodes
│   ├── memory.py                            Checkpointer + RAG retrieval + episodic summarizer
│   ├── agents/
│   │   ├── supervisor.py                       Router node
│   │   └── specialist.py                        Bounded ReAct agent factory
│   ├── tools/
│   │   ├── schemas.py                            Pydantic I/O contracts per domain tool
│   │   ├── mcp_client.py                          MCP call wrapper + mock fallback
│   │   ├── domain_tools.py                         @tool-decorated functions
│   │   └── __init__.py                              Per-agent tool scoping map
│   └── graph/
│       ├── routing.py                                Conditional-edge functions
│       └── build_graph.py                             Assembles the compiled StateGraph
├── evals/
│   ├── dataset.py                LangSmith dataset (happy-path + red-team examples)
│   ├── evaluators.py               Deterministic + LLM-as-judge evaluators
│   └── run_eval.py                  CI entrypoint, enforces MIN_THRESHOLDS
├── k8s/                        Illustrative Kubernetes manifests
└── tests/
    └── test_graph_smoke.py       Offline-runnable smoke tests (no API key required)
```

## 2. The request lifecycle (what actually happens on one user turn)

This is the single most useful mental model for reading the code — trace
one message through the system in the order it's actually executed.

1. **Entry** (`main.py` or, in production, the FastAPI layer sitting in
   front of the voice pipeline) constructs/loads a `MiraState`
   (`mira/state.py::new_session_state`) and appends the new user message.

2. **`mira_graph.invoke(state, config)`** is called
   (`mira/graph/build_graph.py`). LangGraph walks the compiled graph:

   ```
   entry → input_guardrail → supervisor → {one specialist agent}
         → (needs_more_info? loop back to supervisor : continue)
         → output_guardrail → escalate_decision → respond | human_handoff
   ```

3. **`entry_node`** just increments `node_visit_count` (loop-protection
   bookkeeping, see §4).

4. **`input_guardrail_node`** (`mira/guardrails.py`) runs
   `scan_input()` against the latest user message: regex-based PII
   detection (PAN/phone/Aadhaar patterns) and regex-based
   injection/jailbreak pattern matching. Flags are appended to
   `state["guardrail_flags"]` — nothing is blocked at this stage, input
   guardrails only *flag*.

5. **`supervisor_node`** (`mira/agents/supervisor.py`) decides which
   specialist agent handles this turn.
   - **Fast path**: if there's already an `active_agent` and the new
     message doesn't contain a topic-change signal word
     (`_looks_like_continuation`), it stays in the same agent without an
     LLM call at all.
   - **Full path**: otherwise it calls a Tier-1 LLM with
     `.with_structured_output(RouteDecision)` to classify the turn into
     one of 8 domains + a confidence score. Below
     `ROUTING_CONFIDENCE_THRESHOLD` (0.55, `mira/config.py`), it routes to
     `"general"` rather than guessing.

6. **Specialist agent node** (`mira/agents/specialist.py::make_specialist_node`)
   is a factory, not 8 separate files — it closes over `agent_name` and
   returns a node function bound to that domain's tools
   (`mira/tools/__init__.py::ALL_TOOLS_BY_AGENT`) and prompt layer
   (`mira/prompts.py::DOMAIN_LAYERS`). Inside the node:
   - `retrieve_context()` (`mira/memory.py`) pulls RAG chunks scoped to
     the domain.
   - `build_system_prompt()` (`mira/prompts.py`) assembles the layered
     prompt (identity + compliance + domain + voice-style + retrieved
     context + known slots).
   - A bounded loop (max `MAX_TOOL_CALLS_PER_TURN` = 4) alternates
     LLM call → tool call → observation, exactly like a ReAct agent, but
     hand-written so the loop bound and per-call bookkeeping
     (`tool_call_trace`) are explicit rather than hidden inside a
     prebuilt agent executor.
   - Every tool call goes through `mira/tools/domain_tools.py`, which
     validates args against the Pydantic schema in
     `mira/tools/schemas.py`, then calls `mira/tools/mcp_client.py::call_mcp_server`
     — which either hits a real MCP server or (in `MIRA_MCP_MOCK=true`
     mode) calls a deterministic mock function with the same signature.

7. **`after_agent`** (`mira/graph/routing.py`) decides whether to loop
   back to the supervisor (agent asked a clarifying question,
   `needs_more_info=True`) or proceed to the output guardrail. It also
   force-exits to `escalate_decision` if `node_visit_count` exceeds
   `MAX_GRAPH_NODE_VISITS` — the loop-protection backstop.

8. **`output_guardrail_node`** (`mira/guardrails.py`) runs
   `scan_output()` on `state["final_response"]`. This is the mechanical
   enforcement of the design doc's core principle ("the LLM converses,
   it does not compute business truth"): if a numeric/currency pattern
   appears in the response for a regulated domain (`loans`, `insurance`,
   `selling_obv`) **and** no tool was called this turn, the response is
   replaced with a holding message and `needs_more_info=True` is set.

9. **`escalation_decision`** (`mira/graph/routing.py`) routes to
   `human_handoff` if `escalation_flag` is set, if
   `failed_turn_count >= ESCALATION_FAILED_TURN_THRESHOLD`, or if
   routing confidence was very low. Otherwise it routes to `respond`.

10. **`respond_node` / `human_handoff_node`** (`mira/graph/build_graph.py`)
    are the two terminal nodes. `human_handoff_node` builds a structured
    summary (`session_id`, `domain`, `known_slots`, `reason`,
    `tool_calls`) — in production this is what gets pushed to the CCaaS
    queue API.

11. LangGraph's checkpointer (`mira/memory.py::get_checkpointer`)
    persists the full `MiraState` after every node transition, keyed by
    `thread_id` (the `session_id`). This is what makes the next call to
    `mira_graph.invoke()` with the same `thread_id` resume with full
    context rather than starting cold.

## 3. The state object — the one thing every module reads or writes

`MiraState` (`mira/state.py`) is a `TypedDict`, not a class — LangGraph
threads it through the graph as plain data, and every node returns a
**partial dict** of the keys it wants to update (LangGraph merges these
into the running state; see `Annotated[list, add_messages]` on `messages`
for the one field with custom merge behavior — appends rather than
replaces).

| Field | Written by | Read by |
|---|---|---|
| `messages` | entry (new user msg), specialist agent (AI response) | supervisor, specialist agents, guardrails |
| `slots` | specialist agents (not yet wired to auto-extract in this scaffold — see §5) | prompts.py (`KNOWN DETAILS SO FAR` layer) |
| `active_agent` | supervisor | routing.py, specialist agent selection |
| `tool_call_trace` | specialist agents | output guardrail, human_handoff summary, evals |
| `guardrail_flags` | input + output guardrails | evals (`no_unsupported_numeric_claim`) |
| `escalation_flag` / `escalation_reason` | specialist agent (on tool-budget exceeded), output guardrail (implicitly via `needs_more_info`) | escalation_decision |
| `node_visit_count` | entry, supervisor, specialist agents | routing.py loop-protection |

## 4. Where the design-doc guardrails actually live in code

If you're cross-referencing the architecture document against this repo,
here's the direct mapping:

| Design doc concept | Code location |
|---|---|
| Tiered model routing (Ch. 5.1) | `mira/config.py::ModelTier`, `get_chat_model()` |
| Provider failover (Ch. 5, Q3) | `get_chat_model(tier, use_failover=True)` — wire this into a try/except around LLM calls in production; the scaffold exposes the switch but doesn't auto-trigger it |
| Bounded tool-call loop (Ch. 6, Q2) | `mira/agents/specialist.py`, `MAX_TOOL_CALLS_PER_TURN` |
| Routing-loop protection (Ch. 19, Q79) | `mira/graph/routing.py::after_agent`, `MAX_GRAPH_NODE_VISITS` |
| Tool-forcing / no-hallucinated-numbers (Ch. 3.1, 13.2) | `mira/guardrails.py::scan_output` + `no_unsupported_numeric_claim` evaluator |
| Per-agent tool scoping (Ch. 6.2, 11.1) | `mira/tools/__init__.py::ALL_TOOLS_BY_AGENT` |
| Layered system prompts (Ch. 9.1) | `mira/prompts.py` |
| Human handoff with structured summary (Ch. 6.5) | `mira/graph/build_graph.py::human_handoff_node` |
| Explainability (`factors` field) (Ch. 12.2) | `mira/tools/schemas.py::GetOBVValuationOutput.factors` etc. |
| Consent-gated loan eligibility (Ch. 12.4) | `mira/tools/schemas.py::CheckLoanEligibilityInput.consent_credit_check`, enforced in `_mock_loan_eligibility` |

## 5. Known gaps in this scaffold (deliberately out of scope, not bugs)

Documenting these explicitly so nobody mistakes "not implemented" for
"broken":

- **Slot auto-extraction**: `state["slots"]` exists in the schema and is
  read by the prompt layer, but no node currently *writes* structured
  slots from free-text extraction — in production this would be a small
  structured-output LLM call (or parsed from tool call args) added to the
  specialist agent node.
- **Real ASR/TTS/voice gateway**: `main.py` is a text CLI. The voice
  pipeline (Ch. 4 of the design doc) sits in front of this graph in
  production and was intentionally out of scope for this code
  deliverable.
- **Real MCP servers**: `MIRA_MCP_MOCK=true` by default. `mira/tools/mcp_client.py`
  has the real HTTP call path implemented (`httpx.Client(...).post(...)`)
  but it's untested against a live server since none exists in this
  scaffold.
- **Provider failover auto-trigger**: the failover model binding exists
  (`get_chat_model(tier, use_failover=True)`) but nothing currently
  catches a provider exception and retries on it — see
  `PRODUCTION_RUNBOOK.md` §"LLM provider outage" for the recommended
  wiring.
- **Redis/Postgres checkpointer**: defaults to LangGraph's in-memory
  `MemorySaver` so the scaffold runs with zero external infra. The swap
  point and exact production replacement are documented as a comment in
  `mira/memory.py::get_checkpointer`.

## 6. Extension guide — adding a 9th domain

This mirrors Ch. 6 (Q4) of the design doc, worked as an actual code
checklist:

1. Add Pydantic I/O schemas for the new domain's tool(s) to `mira/tools/schemas.py`.
2. Add the `@tool`-decorated function(s) + a mock implementation to `mira/tools/domain_tools.py`.
3. Register the new tool list under a new key in `ALL_TOOLS_BY_AGENT` (`mira/tools/__init__.py`).
4. Add a new prompt entry to `DOMAIN_LAYERS` in `mira/prompts.py`.
5. Add the new domain name to `_DOMAINS` in `mira/agents/supervisor.py` and to its routing description in `_ROUTER_SYSTEM_PROMPT`.
6. Add the new domain to `SPECIALIST_DOMAINS` in `mira/agents/__init__.py` — `build_graph.py` picks this list up automatically and wires the node + conditional edges without further changes.
7. Add at least 2-3 examples (happy path + one adversarial) for the new domain to `evals/dataset.py`.
8. Run `pytest -q` (smoke) and `python -m evals.run_eval` (full regression) before considering it done.

No other file needs to change — this is the concrete proof that the
domain-scoped multi-agent design (Ch. 6.1) achieves what it claims.
