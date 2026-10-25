"""Hallucinated contact details: every phone number and email in the answer must come from
a tool result of this run (or be an official emergency number).

Most facility records have no phone or email, and models under pressure to be helpful
invent plausible ones. A number whose digits only match a stored one after adding or
dropping a leading 0 / country code is not counted as invented, but reported as
"reformatted": the tools say to copy numbers exactly.
"""

from __future__ import annotations

import re

from benchmarking.core.agent_loop import CaseRun
from benchmarking.evaluators.base import BaseEvaluator, EvaluatorResult, tool_output_text

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# A German phone number in an answer: starts with +49, 0049 or 0 and has 7-15 digits in
# total, separated by spaces, dashes, slashes or brackets. Postal codes (5 digits) never match.
ANSWER_PHONE_PATTERN = re.compile(r"(?<![\w+])(?:\+49|0049|0)[\s\-/()]*\d(?:[\s\-/()]*\d){5,13}(?!\w)")

# Any digit run in tool output that could be a stored phone (stored values may lack the 0).
STORED_PHONE_PATTERN = re.compile(r"\+?\d[\d\s\-/()]{4,}\d")

EMERGENCY_NUMBERS = {
    "110", "112", "116016", "08000116016", "08001110111", "08001110222", "0306110300",
    "116111", "08001116111", "116117", "0302900000", "03039063",
}


def normalize_phone(raw: str) -> str:
    """Digits only, +49 / 0049 written as a leading 0."""
    digits = re.sub(r"\D", "", raw)
    if raw.strip().startswith("+49") or digits.startswith("0049"):
        digits = "0" + digits[4:] if digits.startswith("0049") else "0" + digits[2:]
    return digits


class ContactGroundingEvaluator(BaseEvaluator):
    evaluator_name = "contact_grounding"
    metric_name = "Contact_Hallucination_Rate"

    def evaluate(self, run: CaseRun) -> EvaluatorResult | None:
        answer = run.final_answer
        if not answer.strip():
            return None
        source = tool_output_text(run)

        stored_phones = {normalize_phone(p) for p in STORED_PHONE_PATTERN.findall(source)}
        stored_phones = {p for p in stored_phones if len(p) >= 6} | EMERGENCY_NUMBERS
        stored_emails = {e.casefold() for e in EMAIL_PATTERN.findall(source)}

        invented_phones, reformatted = [], []
        for raw in ANSWER_PHONE_PATTERN.findall(answer):
            digits = normalize_phone(raw)
            if digits in stored_phones:
                continue
            if any(len(digits) >= 7 and (digits.lstrip("0") == p.lstrip("0")) for p in stored_phones):
                reformatted.append(raw.strip())
            else:
                invented_phones.append(raw.strip())

        invented_emails = [e for e in EMAIL_PATTERN.findall(answer) if e.casefold() not in stored_emails]

        failures = []
        if invented_phones:
            failures.append(f"phone number(s) not returned by any tool: {invented_phones}")
        if invented_emails:
            failures.append(f"email address(es) not returned by any tool: {invented_emails}")
        hallucinated = bool(failures)
        return self.result(
            not hallucinated, 0.0 if hallucinated else 1.0,
            stage="invented contact details" if hallucinated else "",
            failures=failures,
            warnings=[f"phone number(s) reformatted (leading 0 / country code changed): {reformatted}"] if reformatted else [],
            sub_scores={"hallucination_rate": 1.0 if hallucinated else 0.0,
                        "reformatted_phones": float(len(reformatted))},
            diagnostics={"invented_phones": invented_phones, "invented_emails": invented_emails},
        )
