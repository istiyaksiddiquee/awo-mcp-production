"""The one case format shared by the benchmark and tests/evals.

A case is the `tests/evals/cases.json` format (id, prompt, expect_tool, expect_args,
answer_contains) plus optional fields. Every suite file is a JSON list of cases, so the
pytest evals and the benchmark read the same files:

    {
      "id": "find-help-debt",                     required, unique within a suite
      "prompt": "Ich habe Schulden ...",          required, the user message
      "expect_tool": "find_social_help",          first tool call; a list = any of them;
                                                  null = no expectation
      "expect_args": {"need": "debt"},            args of that first call (see match_value)
      "expect_args_by_tool": {"find_nearby": {"location": "10997"}},
                                                  per-tool args when expect_tool is a list
      "answer_contains": ["646"],                 text the final answer must contain
      "expect_no_results": false,                 the honest answer is "nothing found"
      "requires_hotline": false,                  the answer must give an emergency number
      "requires_tool_call": true,                 answering without any tool call fails
      "expect_iris": [],                          entities a good answer must surface
      "rubric": null,                             instructions for the optional LLM judge
      "status": "active",                         "aspirational" = needs tools that do
      "needs_tools": [],                          not exist yet; skipped unless asked for
      "language": "", "persona": "", "section": "", "source": "",
      "english_translation": "", "notes": ""
    }
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

REPO_ROOT = Path(__file__).resolve().parents[2]

# Suite name -> file. The smoke suite is the pytest eval file, so both stay in sync.
SUITES = {
    "smoke": REPO_ROOT / "tests" / "evals" / "cases.json",
    "citizen": REPO_ROOT / "benchmarking" / "cases" / "citizen_multilingual.json",
    "multilingual": REPO_ROOT / "benchmarking" / "cases" / "multilingual_expanded.json",
    "professional": REPO_ROOT / "benchmarking" / "cases" / "professional_handoff.json",
}


class EvalCase(BaseModel):
    """One benchmark / eval case."""

    model_config = ConfigDict(extra="forbid")

    id: str
    prompt: str
    expect_tool: str | list[str] | None = None
    expect_args: dict[str, Any] = Field(default_factory=dict)
    expect_args_by_tool: dict[str, dict[str, Any]] = Field(default_factory=dict)
    answer_contains: list[str] = Field(default_factory=list)
    expect_no_results: bool = False
    requires_hotline: bool = False
    requires_tool_call: bool = True
    expect_iris: list[str] = Field(default_factory=list)
    rubric: str | None = None
    status: Literal["active", "aspirational"] = "active"
    needs_tools: list[str] = Field(default_factory=list)
    language: str = ""
    persona: str = ""
    section: str = ""
    source: str = ""
    english_translation: str = ""
    notes: str = ""

    # Set by the loader, not by the file.
    suite: str = ""

    @field_validator("expect_tool")
    @classmethod
    def _non_empty_tool_list(cls, value):
        if isinstance(value, list) and not value:
            raise ValueError("expect_tool list must not be empty (use null for 'any tool')")
        return value

    @property
    def expected_tools(self) -> list[str]:
        if self.expect_tool is None:
            return []
        return [self.expect_tool] if isinstance(self.expect_tool, str) else list(self.expect_tool)

    def args_for(self, tool: str) -> dict[str, Any]:
        """Expected arguments when the model chose `tool` (empty = no argument check)."""
        if tool in self.expect_args_by_tool:
            return self.expect_args_by_tool[tool]
        tools = self.expected_tools
        if tools and tool == tools[0]:
            return self.expect_args
        return {}


def resolve_suite_path(name_or_path: str) -> tuple[str, Path]:
    """A suite name from SUITES or a path to a JSON file."""
    if name_or_path in SUITES:
        return name_or_path, SUITES[name_or_path]
    path = Path(name_or_path)
    if not path.is_absolute():
        path = (Path.cwd() / path) if (Path.cwd() / path).exists() else REPO_ROOT / path
    return path.stem, path


def load_suite(name_or_path: str) -> list[EvalCase]:
    """Load one suite and validate every case (ids must be unique)."""
    name, path = resolve_suite_path(name_or_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: a suite is a JSON list of cases")
    cases = []
    for index, item in enumerate(raw):
        try:
            cases.append(EvalCase.model_validate({**item, "suite": name}))
        except Exception as exc:  # noqa: BLE001 - re-raise with the location
            raise ValueError(f"{path}: case #{index} ({item.get('id')!r}) is invalid: {exc}") from exc
    ids = [case.id for case in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"{path}: duplicate case ids {duplicates}")
    return cases


def load_suites(names: list[str], include_aspirational: bool = False) -> list[EvalCase]:
    cases = [case for name in names for case in load_suite(name)]
    if not include_aspirational:
        cases = [case for case in cases if case.status == "active"]
    return cases


# ---- argument matching -----------------------------------------------------------------

def match_value(expected: Any, actual: Any) -> bool:
    """
    Does an actual tool argument satisfy an expected one?

    - string: case-insensitive substring of the actual value (as in tests/evals)
    - number: within 10 % (or 0.02 absolute)
    - bool:   equal
    - list:   every element matches (e.g. several search terms in one argument)
    - {"any_of": [...]}: at least one alternative matches
    """
    if actual is None:
        return False
    if isinstance(expected, dict) and "any_of" in expected:
        return any(match_value(option, actual) for option in expected["any_of"])
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual == expected
    if isinstance(expected, (int, float)):
        try:
            actual_number = float(actual)
        except (TypeError, ValueError):
            return False
        return math.isclose(actual_number, float(expected), rel_tol=0.1, abs_tol=0.02)
    if isinstance(expected, list):
        return all(match_value(item, actual) for item in expected)
    return str(expected).casefold() in str(actual).casefold()


def args_mismatch(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Human-readable problems; empty when every expected argument matches."""
    problems = []
    for key, want in expected.items():
        got = actual.get(key)
        if got is None:
            problems.append(f"missing argument '{key}' (expected ~{want!r})")
        elif not match_value(want, got):
            problems.append(f"argument '{key}'={got!r}, expected {want!r}")
    return problems
