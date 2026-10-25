"""Optional LLM judge (off by default; enable with --judge-model).

Uses the rubric of the datathon directory-search evaluation: literal fulfilment
(complete / useful_partial / not_useful), material factual errors against the tool
results, and material contact risk (a wrong or invented way to reach a service). The
deterministic evaluators stay the pass/fail basis; the judge is reported next to them,
because AI grading is single-rater and can be wrong.
"""

from __future__ import annotations

import json
import re

from benchmarking.core.agent_loop import CaseRun, transcript_text
from benchmarking.core.model_adapter import LiteLLMChatModel
from benchmarking.evaluators.base import EvaluatorResult

JUDGE_INSTRUCTIONS = """You grade one answer of an assistant that uses tools over a directory of social services.
Judge ONLY against the user's request and the tool results shown. Do not use outside knowledge
about the services.

Return JSON only, with these keys:
- "literal_fulfillment": "complete" (every part of the request is answered as asked),
  "useful_partial" (useful but something asked for is missing or only partly done) or
  "not_useful".
- "material_factual_error": true if the answer states something that contradicts or is not
  supported by the tool results AND it would matter to the user (wrong address, wrong hours,
  a service that does not exist, wrong count).
- "material_contact_risk": true if a phone number, email or address in the answer is invented,
  changed, or attributed to the wrong office, so the user could contact the wrong place.
- "rationale": at most two sentences.
"""

LEVELS = {"complete": 1.0, "useful_partial": 0.5, "not_useful": 0.0}


class RubricJudgeEvaluator:
    evaluator_name = "rubric_judge"
    metric_name = "Judge_Literal_Fulfillment"

    def __init__(self, judge: LiteLLMChatModel) -> None:
        self.judge = judge

    async def evaluate(self, run: CaseRun) -> EvaluatorResult | None:
        if not run.final_answer.strip():
            return None
        prompt = (
            f"{JUDGE_INSTRUCTIONS}\n"
            + (f"Case-specific rubric: {run.case.rubric}\n" if run.case.rubric else "")
            + f"\nUSER REQUEST:\n{run.case.prompt}\n"
            + f"\nTOOL CALLS AND RESULTS:\n{transcript_text(run, max_chars=6000) or '(no tool calls)'}\n"
            + f"\nANSWER TO GRADE:\n{run.final_answer}\n"
        )
        response = await self.judge.complete([{"role": "user", "content": prompt}], tools=[])
        if not response.success or not response.assistant_content:
            return EvaluatorResult(evaluator_name=self.evaluator_name, metric_name=self.metric_name,
                                   passed=False, score=0.0, stage="judge failed",
                                   failures=[f"judge failed: {response.error_message}"])
        verdict = _parse_json(response.assistant_content)
        level = str(verdict.get("literal_fulfillment", "not_useful"))
        factual = bool(verdict.get("material_factual_error", False))
        contact = bool(verdict.get("material_contact_risk", False))
        score = LEVELS.get(level, 0.0)
        passed = level == "complete" and not factual and not contact
        failures = []
        if level != "complete":
            failures.append(f"judge: {level}")
        if factual:
            failures.append("judge: material factual error")
        if contact:
            failures.append("judge: material contact risk")
        return EvaluatorResult(
            evaluator_name=self.evaluator_name, metric_name=self.metric_name, passed=passed,
            score=score, stage="" if passed else "judge: " + (level if level != "complete" else "error/risk"),
            failures=failures,
            sub_scores={"complete": 1.0 if level == "complete" else 0.0,
                        "material_factual_error": 1.0 if factual else 0.0,
                        "material_contact_risk": 1.0 if contact else 0.0},
            diagnostics={"verdict": verdict, "judge_model": self.judge.cfg.id},
        )


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(match.group(0)) if match else {}
    except ValueError:
        return {}
