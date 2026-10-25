"""The benchmark against the real server, offline: suites match the server's tools, and an
oracle run goes through the MCP client, agent loop, evaluators and reports end to end."""

import asyncio
import json

import pytest

from benchmarking.core.cases import SUITES, load_suites
from benchmarking.run_benchmark import main, validate_cases
from tests.helpers import mcp_list_tools


@pytest.fixture(scope="module")
def server_tools():
    return [tool.name for tool in mcp_list_tools()]


def test_every_active_case_can_be_run_on_this_server(server_tools):
    cases = load_suites(sorted(SUITES), include_aspirational=True)
    problems = validate_cases(cases, server_tools)
    assert not problems, "benchmark cases that cannot run on server.py:\n  " + "\n  ".join(problems)


def test_aspirational_cases_really_need_missing_tools(server_tools):
    cases = load_suites(sorted(SUITES), include_aspirational=True)
    for case in cases:
        if case.status == "aspirational":
            existing = [t for t in case.needs_tools if t in server_tools]
            assert not existing, (
                f"{case.suite}/{case.id} waits for {existing}, which server.py now has: make it active"
            )


def test_oracle_benchmark_runs_end_to_end_offline(tmp_path):
    code = main(["--offline", "--models", "oracle", "--suites", "smoke",
                 "--cases", "find-help-debt,entity-card-by-iri,service-candidates-with-office",
                 "--tool-profiles", "baseline,all", "--concurrency", "1", "--quiet",
                 "--output-dir", str(tmp_path)])
    assert code == 0
    [report] = tmp_path.glob("benchmark-*.json")
    data = json.loads(report.read_text(encoding="utf-8"))
    passed = {(r["tool_profile"], r["case_id"]): r["passed"] for r in data["runs"]}
    assert passed[("all", "find-help-debt")] and passed[("all", "entity-card-by-iri")]
    assert passed[("baseline", "find-help-debt")]
    assert not passed[("baseline", "entity-card-by-iri")], "baseline profile must hide get_entity_card"
    assert {p["change"] for p in data["paired"]} == {"gain", "same"}
    assert list(tmp_path.glob("leaderboard-*.csv")) and list(tmp_path.glob("paired-*.csv"))
