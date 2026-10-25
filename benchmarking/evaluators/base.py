"""Evaluator interface: every evaluator grades one CaseRun, or says it does not apply."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from benchmarking.core.agent_loop import CaseRun


class EvaluatorResult(BaseModel):
    """Score of one evaluator for one run."""

    model_config = ConfigDict(extra="forbid")

    evaluator_name: str
    metric_name: str
    passed: bool
    score: float = Field(..., ge=0.0, le=1.0)
    stage: str = Field(default="", description="awo-style failure stage when passed is False")
    sub_scores: dict[str, float] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class BaseEvaluator(ABC):
    evaluator_name: str = "base"
    metric_name: str = ""

    @abstractmethod
    def evaluate(self, run: CaseRun) -> EvaluatorResult | None:
        """Grade the run; None when this evaluator does not apply to the case."""

    def result(self, passed: bool, score: float, **kwargs: Any) -> EvaluatorResult:
        return EvaluatorResult(
            evaluator_name=self.evaluator_name, metric_name=self.metric_name,
            passed=passed, score=round(min(1.0, max(0.0, score)), 4), **kwargs,
        )


def tool_output_text(run: CaseRun) -> str:
    """Everything the tools returned in this run (what the answer may be grounded in)."""
    return "\n".join(r.text for r in run.tool_results if r.success)
