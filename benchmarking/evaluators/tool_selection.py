"""Tool choice, argument schema and argument values of the model's FIRST tool call.

Like tests/evals, only the first call is scored: it shows whether the model understood
the request. Arguments are validated against the input schema the live server reports,
and compared with the case's expected arguments (see cases.match_value).
"""

from __future__ import annotations

from typing import Any

from benchmarking.core.agent_loop import CaseRun
from benchmarking.core.cases import args_mismatch
from benchmarking.evaluators.base import BaseEvaluator, EvaluatorResult


def check_json_type(value: Any, schema: dict[str, Any]) -> bool:
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return any(check_json_type(value, sub) for sub in schema[key])
    kind = schema.get("type")
    if isinstance(kind, list):
        return any(check_json_type(value, {**schema, "type": k}) for k in kind)
    if kind == "null":
        return value is None
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return True


def schema_errors(arguments: dict[str, Any], schema: dict[str, Any] | None) -> list[str]:
    if schema is None:
        return ["tool has no schema on the server"]
    errors = []
    properties = schema.get("properties", {})
    for key in schema.get("required", []):
        if arguments.get(key) is None:
            errors.append(f"missing required argument '{key}'")
    for key, value in arguments.items():
        if key not in properties:
            if schema.get("additionalProperties") is False:
                errors.append(f"unknown argument '{key}'")
            continue
        if not check_json_type(value, properties[key]):
            errors.append(f"argument '{key}'={value!r} does not match {properties[key].get('type', properties[key])}")
    return errors


class ToolSelectionEvaluator(BaseEvaluator):
    evaluator_name = "tool_selection"
    metric_name = "Tool_Selection_Accuracy"

    def __init__(self, schemas: dict[str, dict[str, Any]]) -> None:
        self.schemas = schemas

    def evaluate(self, run: CaseRun) -> EvaluatorResult | None:
        case = run.case
        expected = [t for t in case.expected_tools if t in run.offered_tools] or case.expected_tools
        if not expected and not case.requires_tool_call:
            return None

        if not run.tool_calls:
            if not case.requires_tool_call:
                return None
            return self.result(False, 0.0, stage="model did not call a tool",
                               failures=["the model answered without calling a tool"],
                               sub_scores={"tool_name_accuracy": 0.0, "schema_validity": 0.0, "argument_match": 0.0})

        first = run.tool_calls[0]
        name_ok = not expected or first.tool_name in expected
        failures, stage = [], ""

        if first.tool_name not in run.offered_tools:
            stage = "model called a tool that does not exist"
            failures.append(f"'{first.tool_name}' is not one of the offered tools")
        elif not name_ok:
            stage = "wrong tool chosen"
            failures.append(f"expected {' or '.join(expected)}, model chose '{first.tool_name}'")

        if first.parse_error:
            schema_problems = [first.parse_error]
        else:
            schema_problems = schema_errors(first.arguments, self.schemas.get(first.tool_name))
        if schema_problems and not stage:
            stage = "wrong tool arguments"
        failures += schema_problems

        argument_problems = args_mismatch(case.args_for(first.tool_name), first.arguments) if name_ok else []
        if argument_problems and not stage:
            stage = "wrong tool arguments"
        failures += argument_problems

        expected_args = case.args_for(first.tool_name) if name_ok else {}
        arg_score = 1.0 if not expected_args else 1.0 - len(argument_problems) / len(expected_args)
        if not name_ok:
            arg_score = 0.0
        schema_score = 0.0 if schema_problems else 1.0
        name_score = 1.0 if name_ok and first.tool_name in run.offered_tools else 0.0

        return self.result(
            name_score == 1.0 and schema_score == 1.0 and not argument_problems,
            0.5 * name_score + 0.2 * schema_score + 0.3 * arg_score,
            stage=stage, failures=failures,
            sub_scores={"tool_name_accuracy": name_score, "schema_validity": schema_score,
                        "argument_match": round(arg_score, 4)},
            diagnostics={"first_call": first.tool_name, "arguments": first.arguments,
                         "expected_tools": expected, "all_calls": [c.tool_name for c in run.tool_calls]},
        )
