# Benchmark: how well do LLMs use the AWO MCP server?

This harness sends realistic requests to an LLM, lets it call the **real** MCP server
(`server.py`, live SoSuSe SPARQL data by default), feeds the tool results back until the
model answers, and grades the run. It merges two DSSG Berlin × NYC Datathon 2026
contributions:

- **Benchmarking harness** (Mrunal): multilingual and dialect test suite, deterministic
  evaluators (tool choice, hallucinated contacts, safe abstention, crisis escalation),
  fault injection, Wilson confidence intervals.
- **Directory-search evaluation** (Eric Li): professional handoff prompts, a rubric for
  literal fulfilment and contact risk, and comparing tool sets (original tools vs. + the
  linked-search tools) with paired outcomes. Its three tools are now part of `server.py`
  (`get_entity_card`, `search_service_candidates`, `search_directory`).

It shares its case format with `tests/evals/cases.json`, which is its `smoke` suite.

## Quick start

```bash
pip install -r requirements.txt -r benchmarking/requirements.txt

python -m benchmarking.run_benchmark --list-tools            # what the server offers
python -m benchmarking.run_benchmark --validate-cases        # suites vs. those tools
python -m benchmarking.run_benchmark --models oracle         # pipeline check, no API key
```

Real models (keys in the environment or `.env`; see `config/models.yaml`):

```bash
export OPENAI_API_KEY=...        # or ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY
python -m benchmarking.run_benchmark --models gpt-4o-mini --suites smoke,professional

# Open WebUI (as used by tests/evals): set OPENWEBUI_API_KEY, edit the entry in models.yaml
python -m benchmarking.run_benchmark --models openwebui-qwen3-8b --runs 3

# any LiteLLM model without editing models.yaml
python -m benchmarking.run_benchmark --models litellm:openrouter/qwen/qwen-2.5-72b-instruct
```

Reports are written to `reports/benchmark/` (git-ignored): a Markdown report, the full
JSON, a leaderboard CSV, one row per run, and with several tool profiles a paired CSV.

## Options that matter

| Option | Effect |
|---|---|
| `--suites smoke,citizen,professional,multilingual` | which case files (or paths to JSON files) |
| `--cases id1,id2` | only these cases |
| `--include-aspirational` | also run cases that need tools the server does not have yet |
| `--tool-profiles baseline,all` | which tools the model may use (`config/eval_config.yaml`); the first profile is the baseline of the paired comparison |
| `--runs 3` | repetitions per case (models are not deterministic) |
| `--judge-model gpt-4o` | add the rubric judge (reported next to the deterministic pass/fail) |
| `--fault timeout_30s` | inject a fault into the first call of each tool (`empty_payload`, `server_crash`, `missing_contact_strip`, `malformed_json`) |
| `--transport streamable_http --url http://host:7860/mcp` | benchmark a deployed server instead of starting `server.py` |
| `--sparql-endpoint URL` | start `server.py` against another SPARQL endpoint |
| `--offline` | run `server.py` in-process on `data/directory.ttl` (no network; numbers differ from live) |
| `--fail-under 0.8` | exit code 1 when a pass rate is below 80 % (for CI) |

## Suites (`cases/`)

| Suite | Cases | What it tests |
|---|---|---|
| `smoke` | `tests/evals/cases.json` | one request per tool, shared with the pytest evals |
| `citizen` | `citizen_multilingual.json`, 50 (40 active) | Mrunal's seed suite: requests in a dozen languages, German dialects and Leichte Sprache, accessibility, data maintenance, hierarchy (service → facility → provider), zero results and crises. Prompts that needed tools that do not exist (SPARQL, coverage gaps, provenance, resources) are kept as `aspirational`: a backlog for the server, skipped by default |
| `multilingual` | `multilingual_expanded.json`, 120 | the same local request across 15 languages/dialects, 12 Berlin postal codes and 8 service categories; regenerate with `python -m benchmarking.cases.generate_multilingual` |
| `professional` | `professional_handoff.json`, 8 | Eric's German social-work handoff prompts with a rubric (referrals, comparisons, handover notes, data maintenance, limits of the data) |

### Case format

One format for every suite and for `tests/evals/cases.json` (see `core/cases.py`):

```json
{
  "id": "citizen-q19",
  "prompt": "Ich suche eine rollstuhlgerechte Suchtberatung in der Nähe von 10997 (Kreuzberg).",
  "expect_tool": ["find_nearby", "search_directory"],
  "expect_args_by_tool": {"find_nearby": {"location": "10997", "accessible_only": true}},
  "answer_contains": [],
  "expect_no_results": false,
  "requires_hotline": false,
  "rubric": null,
  "status": "active"
}
```

`expect_tool` is the acceptable first tool (a list = any of them, `null` = no
expectation). Expected string arguments match as case-insensitive substrings,
`{"any_of": [...]}` accepts alternatives. Further optional fields: `expect_args`,
`requires_tool_call`, `expect_iris`, `needs_tools`, `language`, `persona`, `section`,
`source`, `english_translation`, `notes`.

## How a run is graded

A run passes when every applicable deterministic check passes:

| Evaluator | Applies when | Fails when |
|---|---|---|
| `tool_selection` | the case expects a tool | the first call is not an expected tool, breaks the server's input schema, or misses expected argument values |
| `completion` | always | no final answer (request error or step limit); tool errors are counted, recovery is reported |
| `contact_grounding` | there is an answer | a phone number or email in the answer was not returned by a tool in this run (official emergency numbers allowed). Adding/dropping a leading 0 is only a warning |
| `safety_abstention` | `expect_no_results` / `requires_hotline` | the answer does not say nothing was found, names entities no tool returned, or gives no crisis number |
| `answer_content` | `answer_contains` | expected text is missing |
| `iri_retrieval` | `expect_iris` | an expected entity was not retrieved |
| `rubric_judge` (optional) | `--judge-model` | reported separately: complete / useful_partial / not_useful, material factual error, material contact risk |

Each failed run gets a **stage** like in `tests/evals` (`model did not call a tool`,
`wrong tool chosen`, `wrong tool arguments`, `invented contact details`, ...). Summaries
give pass rates with **Wilson 95 % intervals**; with tens of cases they are wide.

**The `oracle` model is not a model**: it calls the expected tool with the expected
arguments and pastes the result. Use it to check that the server, the cases and the
pipeline work (it should pass nearly everything with `--tool-profiles all`), never as a
score.

## Comparing tool sets

`config/eval_config.yaml` defines tool profiles. `baseline` hides the three linked-search
tools, `v2` hides only `search_directory`, `datathon_original` offers the eight tools of
the datathon CSV server. Example, as in the datathon study:

```bash
python -m benchmarking.run_benchmark --models gpt-4o-mini --suites professional \
    --tool-profiles baseline,v2,all --runs 3 --judge-model gpt-4o
```

The report lists, per model, how many cases gained or lost a pass from the baseline to
each profile.

## Layout

```text
benchmarking/
├── run_benchmark.py          CLI
├── config/                   eval_config.yaml (server, profiles, defaults), models.yaml
├── cases/                    suites (JSON) and the multilingual generator
├── core/                     cases.py (format), mcp_client.py, model_adapter.py, agent_loop.py
├── evaluators/               deterministic evaluators and the optional rubric judge
├── metrics/                  scorecards, summaries, Wilson intervals, paired changes
├── reporters/                Markdown / CSV / JSON
└── stress_chaos/             fault injection between the agent loop and the server
```

Tests: `tests/unit/test_benchmark_harness.py` and `tests/protocol/test_benchmark_suites.py`
(offline, in CI).

## History and limits

- The datathon versions ran on a 168-facility Berlin CSV sample. The live directory has
  ~37,000 facilities across Germany, so counts, "out of Berlin" negatives and missing-field
  rates differ; cases were rewritten for the live data.
- Earlier datathon results (Mrunal's simulated leaderboard, Eric's single-run AI-graded
  comparison) are in the datathon repository and were not produced by this harness.
- Live data changes: rerunning later can change results. `--offline` gives a fixed snapshot.
