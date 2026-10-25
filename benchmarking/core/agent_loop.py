"""The agent loop: prompt -> tool calls on the real MCP server -> results back -> answer.

Same flow as tests/evals/openwebui.py, but it does not stop at the first problem: an
unknown tool, malformed arguments or a tool error is sent back to the model as the tool
result (as MCP client apps do), so recovery is measured. The final answer is the first
assistant message without tool calls; the evaluators grade it.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from benchmarking.core.cases import EvalCase
from benchmarking.core.mcp_client import MCPToolExecutionResult
from benchmarking.core.model_adapter import (
    DEFAULT_SYSTEM_PROMPT, InterceptedToolCall, ModelInvocationResult, TokenUsageMetrics,
)

# Characters of a tool result passed back to the model. Larger results are cut, and the
# model is told so (find_nearby / search_facilities on a big city can be very large).
MAX_TOOL_RESULT_CHARS = 30_000


class ChatModel(Protocol):
    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelInvocationResult: ...


class ToolRunner(Protocol):
    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None,
                        timeout_seconds: float | None = None) -> MCPToolExecutionResult: ...


class CaseRun(BaseModel):
    """Everything that happened while one model answered one case."""

    model_config = ConfigDict(extra="forbid")

    case: EvalCase
    model_id: str
    tool_profile: str
    run_index: int
    offered_tools: list[str]
    tool_calls: list[InterceptedToolCall] = Field(default_factory=list)
    tool_results: list[MCPToolExecutionResult] = Field(default_factory=list)
    final_answer: str = ""
    steps: int = 0
    loop_error: str = ""
    usage: TokenUsageMetrics = Field(default_factory=TokenUsageMetrics)
    ttft_ms: float = 0.0
    model_latency_ms: float = 0.0
    tool_latency_ms: float = 0.0
    wall_seconds: float = 0.0
    messages: list[dict[str, Any]] = Field(default_factory=list)


def tool_message(call: InterceptedToolCall, result: MCPToolExecutionResult) -> dict[str, Any]:
    if result.success:
        content = result.text
    else:
        content = f"ERROR: {result.error_message or result.text or 'tool failed'}"
    if len(content) > MAX_TOOL_RESULT_CHARS:
        content = content[:MAX_TOOL_RESULT_CHARS] + (
            f"\n[... result cut after {MAX_TOOL_RESULT_CHARS} characters; narrow the search or use paging]"
        )
    return {"role": "tool", "tool_call_id": call.call_id, "content": content}


async def run_case(
    model: ChatModel,
    tools_runner: ToolRunner,
    case: EvalCase,
    tools: list[dict[str, Any]],
    *,
    model_id: str,
    tool_profile: str = "all",
    run_index: int = 1,
    max_steps: int = 6,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> CaseRun:
    offered = [t["function"]["name"] for t in tools]
    run = CaseRun(case=case, model_id=model_id, tool_profile=tool_profile, run_index=run_index, offered_tools=offered)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": case.prompt},
    ]
    start = time.perf_counter()

    for step in range(1, max_steps + 1):
        run.steps = step
        response = await model.complete(messages, tools)
        run.usage = run.usage + response.usage
        run.model_latency_ms += response.roundtrip_latency_ms
        if step == 1:
            run.ttft_ms = response.ttft_ms
        if not response.success:
            run.loop_error = f"model request failed: {response.error_message}"
            break

        messages.append(response.assistant_message())
        if not response.intercepted_tool_calls:
            run.final_answer = response.assistant_content or ""
            if not run.final_answer.strip():
                run.loop_error = "model gave no final answer (empty reply)"
            break

        for call in response.intercepted_tool_calls:
            run.tool_calls.append(call)
            if call.parse_error:
                result = MCPToolExecutionResult(tool_name=call.tool_name, arguments={}, success=False,
                                                is_error=True, latency_ms=0.0, error_message=call.parse_error)
            elif call.tool_name not in offered:
                result = MCPToolExecutionResult(
                    tool_name=call.tool_name, arguments=call.arguments, success=False, is_error=True,
                    latency_ms=0.0, error_message=f"Unknown tool '{call.tool_name}'. Available: {', '.join(offered)}")
            else:
                result = await tools_runner.call_tool(call.tool_name, call.arguments)
            run.tool_results.append(result)
            run.tool_latency_ms += result.latency_ms
            messages.append(tool_message(call, result))
    else:
        run.loop_error = f"model gave no final answer after {max_steps} steps"

    run.wall_seconds = round(time.perf_counter() - start, 2)
    run.messages = messages
    return run


def transcript_text(run: CaseRun, max_chars: int = 4000) -> str:
    """Compact transcript for reports and the judge."""
    lines = []
    for call, result in zip(run.tool_calls, run.tool_results):
        lines.append(f"CALL {call.tool_name}({json.dumps(call.arguments, ensure_ascii=False)})")
        body = result.text if result.success else f"ERROR {result.error_message}"
        lines.append(f"RESULT {body[:max_chars]}")
    return "\n".join(lines)
