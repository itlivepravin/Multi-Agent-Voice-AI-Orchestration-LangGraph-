"""
CI entry point (Ch. 14.5's "AI-specific CI gate" / Ch. 16.5): runs the full
MIRA graph against the LangSmith dataset and reports pass/fail against
minimum thresholds. Wired into the pipeline so a prompt, model, or
graph-logic change that regresses these scores blocks merge.

Usage:
    python -m evals.run_eval
"""
from __future__ import annotations

import sys
import uuid

from langsmith import Client
from langsmith.evaluation import evaluate

from evals.dataset import DATASET_NAME, upload_dataset
from evals.evaluators import ALL_EVALUATORS
from mira.graph import mira_graph
from mira.state import new_session_state

# Ch. 16.5 — minimum passing thresholds agreed with product/compliance, not
# just an engineering-internal bar.
MIN_THRESHOLDS = {
    "tool_call_correctness": 0.95,
    "domain_routing_correctness": 0.90,
    "no_unsupported_numeric_claim": 1.0,   # hard gate — Ch. 3.1 / 13.2, zero tolerance
    "escalation_correctness": 0.90,
    "groundedness_and_tone": 0.85,
}


def target(inputs: dict) -> dict:
    session_id = str(uuid.uuid4())
    state = new_session_state(
        session_id=session_id,
        persona=inputs.get("persona", "consumer"),
        channel="text",
    )
    from langchain_core.messages import HumanMessage
    state["messages"] = [HumanMessage(content=inputs["text"])]
    result = mira_graph.invoke(state, config={"configurable": {"thread_id": session_id}})
    return dict(result)


def main() -> int:
    client = Client()
    upload_dataset(client)

    results = evaluate(
        target,
        data=DATASET_NAME,
        evaluators=ALL_EVALUATORS,
        experiment_prefix="mira-regression",
        client=client,
    )

    # Aggregate scores per evaluator key and check against MIN_THRESHOLDS.
    scores: dict[str, list[float]] = {}
    for row in results:
        for eval_result in row.get("evaluation_results", {}).get("results", []):
            scores.setdefault(eval_result.key, []).append(eval_result.score or 0)

    failed = []
    for key, threshold in MIN_THRESHOLDS.items():
        values = scores.get(key, [])
        avg = sum(values) / len(values) if values else 0.0
        status = "PASS" if avg >= threshold else "FAIL"
        print(f"{key}: {avg:.2%} (threshold {threshold:.0%}) — {status}")
        if avg < threshold:
            failed.append(key)

    if failed:
        print(f"\nEvaluation FAILED — regressed on: {', '.join(failed)}")
        return 1

    print("\nAll evaluation gates passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
