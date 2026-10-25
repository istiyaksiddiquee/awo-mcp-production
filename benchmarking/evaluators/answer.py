"""Did the run finish, does the answer contain what it must, and did it surface the right entities?"""

from __future__ import annotations

import re

from benchmarking.core.agent_loop import CaseRun
from benchmarking.evaluators.base import BaseEvaluator, EvaluatorResult, tool_output_text

IRI_PATTERN = re.compile(r"https://civic-data\.de/federated-directory#[A-Za-z0-9_-]+")


class CompletionEvaluator(BaseEvaluator):
    """A final answer exists; tool errors (real or injected) were recovered from."""

    evaluator_name = "completion"
    metric_name = "Completion_And_Error_Recovery"

    def evaluate(self, run: CaseRun) -> EvaluatorResult:
        failed = [i for i, r in enumerate(run.tool_results) if not r.success]
        # Recovered = despite failed calls, the run still ended in an answer.
        recovered = bool(run.final_answer.strip()) and not run.loop_error
        sub = {
            "completed": 1.0 if run.final_answer.strip() and not run.loop_error else 0.0,
            "tool_errors": float(len(failed)),
            "error_recovery": (1.0 if recovered else 0.0) if failed else 1.0,
        }
        if run.loop_error:
            stage = "model request failed" if run.loop_error.startswith("model request failed") else "model gave no final answer"
            return self.result(False, 0.0, stage=stage, failures=[run.loop_error], sub_scores=sub)
        if not run.final_answer.strip():
            return self.result(False, 0.0, stage="model gave no final answer", failures=["empty answer"], sub_scores=sub)
        warnings = [f"{len(failed)} tool call(s) failed: " + "; ".join(
            (run.tool_results[i].error_message or "")[:120] for i in failed)] if failed else []
        return self.result(True, 1.0, sub_scores=sub, warnings=warnings)


class AnswerContentEvaluator(BaseEvaluator):
    """answer_contains: every listed text appears in the final answer (case-insensitive)."""

    evaluator_name = "answer_content"
    metric_name = "Answer_Contains"

    def evaluate(self, run: CaseRun) -> EvaluatorResult | None:
        wanted = run.case.answer_contains
        if not wanted:
            return None
        answer = run.final_answer.casefold()
        missing = [text for text in wanted if text.casefold() not in answer]
        return self.result(
            not missing, 1.0 - len(missing) / len(wanted),
            stage="final answer missing expected content" if missing else "",
            failures=[f"final answer does not contain {missing}"] if missing else [],
        )


class IriRetrievalEvaluator(BaseEvaluator):
    """expect_iris: the expected entities were retrieved by a tool (recall) and named in the answer."""

    evaluator_name = "iri_retrieval"
    metric_name = "Entity_Recall"

    def evaluate(self, run: CaseRun) -> EvaluatorResult | None:
        expected = run.case.expect_iris
        if not expected:
            return None
        retrieved = set(IRI_PATTERN.findall(tool_output_text(run)))
        hits = [iri for iri in expected if iri in retrieved]
        recall = len(hits) / len(expected)
        missing = sorted(set(expected) - set(hits))
        return self.result(
            recall == 1.0, recall,
            stage="expected entities not retrieved" if missing else "",
            failures=[f"not retrieved by any tool: {missing}"] if missing else [],
            sub_scores={"recall": round(recall, 4)},
        )
