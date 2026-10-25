"""Fault Injection Middleware for MCP Operational Resilience Benchmarking.

Intercepts `call_tool()` invocations between the LLM / evaluation runner and the
target MCP server (or standalone mock backend) to inject deterministic faults:
1. `timeout_30s`: Simulates a 30-second SPARQL / tool execution hang and returns
   a structured `MCPToolExecutionResult(timed_out=True, is_error=True)`.
2. `empty_payload`: Returns an empty JSON payload array `[]` to test zero-result
   abstention and fallback behavior without fabricating entities.
3. `server_crash`: Simulates an abrupt MCP server crash / JSON-RPC transport
   disconnect (`MCPServerCrashError: [Errno 32] Broken pipe / EOF on stdio stream`).
4. `missing_contact_strip`: Strips `schema:telephone` and `schema:email` fields from
   returned records to enforce the ~80% null-contact trap.
5. `malformed_json`: Emits truncated / corrupted JSON output to verify client/agent
   error handling.
"""

from __future__ import annotations

import asyncio
import json
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from benchmarking.core.mcp_client import MCPBenchmarkClient, MCPToolExecutionResult


class FaultMode(str, Enum):
    """Supported operational fault injection modes."""

    NONE = "none"
    TIMEOUT_30S = "timeout_30s"
    EMPTY_PAYLOAD = "empty_payload"
    SERVER_CRASH = "server_crash"
    MISSING_CONTACT_STRIP = "missing_contact_strip"
    MALFORMED_JSON = "malformed_json"


class FaultInjectionConfig(BaseModel):
    """Configuration for deterministic or probability-based fault injection."""

    model_config = ConfigDict(extra="forbid")

    mode: FaultMode = Field(default=FaultMode.NONE, description="Fault mode to inject")
    target_tools: list[str] | None = Field(
        default=None,
        description="If set, only inject faults when one of these tools is called (None = all tools)",
    )
    simulated_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Reported timeout duration in seconds (e.g., 30.0s SPARQL limit)",
    )
    fast_forward_sleep_seconds: float = Field(
        default=0.05,
        ge=0.0,
        description="Actual wall-clock sleep during simulated timeouts so test suites complete rapidly",
    )
    fail_first_n_calls: int = Field(
        default=1,
        ge=1,
        description="Number of initial calls to fault before allowing retry recovery to succeed",
    )


class FaultSimulationReport(BaseModel):
    """Audit record of faults injected during a benchmark run."""

    model_config = ConfigDict(extra="forbid")

    total_calls_intercepted: int = 0
    faults_injected: int = 0
    recovery_attempts_observed: int = 0
    successful_recoveries: int = 0
    events: list[dict[str, Any]] = Field(default_factory=list)


class FaultInjectorMiddleware:
    """Middleware wrapping `MCPBenchmarkClient` (or acting as a standalone fault proxy) for chaos testing."""

    def __init__(
        self,
        config: FaultInjectionConfig,
        downstream_client: MCPBenchmarkClient | None = None,
    ) -> None:
        self.config = config
        self.downstream_client = downstream_client
        self._call_counts_by_tool: dict[str, int] = {}
        self.report = FaultSimulationReport()

    def reset_counters(self) -> None:
        """Reset per-tool call counters and simulation report."""
        self._call_counts_by_tool.clear()
        self.report = FaultSimulationReport()

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> MCPToolExecutionResult:
        """Intercept an MCP tool invocation and inject the configured fault or forward downstream."""
        args = arguments or {}
        self.report.total_calls_intercepted += 1
        call_num = self._call_counts_by_tool.get(tool_name, 0) + 1
        self._call_counts_by_tool[tool_name] = call_num

        should_inject = self._should_inject_fault(tool_name, call_num, args)
        if not should_inject:
            if call_num > 1:
                self.report.recovery_attempts_observed += 1
            res = await self._forward_or_mock_success(tool_name, args, timeout_seconds)
            if call_num > 1 and res.success:
                self.report.successful_recoveries += 1
            return res

        self.report.faults_injected += 1
        mode = self.config.mode
        start_perf = time.perf_counter()

        if mode == FaultMode.TIMEOUT_30S:
            if self.config.fast_forward_sleep_seconds > 0:
                await asyncio.sleep(self.config.fast_forward_sleep_seconds)
            simulated_ms = self.config.simulated_timeout_seconds * 1000.0
            err_msg = (
                f"MCPTimeoutError: Tool '{tool_name}' exceeded the "
                f"{self.config.simulated_timeout_seconds:.1f}s execution limit. "
                "Hint: Add a LIMIT clause or narrow your filter criteria."
            )
            self._record_event(tool_name, call_num, mode, err_msg)
            return MCPToolExecutionResult(
                tool_name=tool_name,
                arguments=args,
                success=False,
                is_error=True,
                latency_ms=simulated_ms,
                raw_text_outputs=[err_msg],
                parsed_json_output={"error": "TIMEOUT_EXCEEDED", "timeout_seconds": self.config.simulated_timeout_seconds},
                error_message=err_msg,
                timed_out=True,
            )

        if mode == FaultMode.EMPTY_PAYLOAD:
            latency_ms = (time.perf_counter() - start_perf) * 1000.0
            self._record_event(tool_name, call_num, mode, "Returned empty JSON array []")
            return MCPToolExecutionResult(
                tool_name=tool_name,
                arguments=args,
                success=True,
                is_error=False,
                latency_ms=round(latency_ms, 2),
                raw_text_outputs=["[]"],
                parsed_json_output=[],
            )

        if mode == FaultMode.SERVER_CRASH:
            latency_ms = (time.perf_counter() - start_perf) * 1000.0
            crash_msg = (
                f"MCPServerCrashError: Transport connection lost while executing '{tool_name}' "
                "(JSON-RPC -32000: Broken pipe / unexpected EOF from server process)."
            )
            self._record_event(tool_name, call_num, mode, crash_msg)
            return MCPToolExecutionResult(
                tool_name=tool_name,
                arguments=args,
                success=False,
                is_error=True,
                latency_ms=round(latency_ms, 2),
                raw_text_outputs=[crash_msg],
                parsed_json_output={"jsonrpc": "2.0", "error": {"code": -32000, "message": crash_msg}},
                error_message=crash_msg,
            )

        if mode == FaultMode.MALFORMED_JSON:
            latency_ms = (time.perf_counter() - start_perf) * 1000.0
            corrupted = '[{"iri": "https://civic-data.de/federated-directory#org-00416a783a9b", "schema:name": "Truncated'
            err_msg = "JSONDecodeError: Unterminated string starting at line 1 column 78"
            self._record_event(tool_name, call_num, mode, err_msg)
            return MCPToolExecutionResult(
                tool_name=tool_name,
                arguments=args,
                success=False,
                is_error=True,
                latency_ms=round(latency_ms, 2),
                raw_text_outputs=[corrupted],
                parsed_json_output=None,
                error_message=err_msg,
            )

        if mode == FaultMode.MISSING_CONTACT_STRIP:
            base_res = await self._forward_or_mock_success(tool_name, args, timeout_seconds)
            stripped_payload = self._strip_contact_fields(base_res.parsed_json_output)
            stripped_text = json.dumps(stripped_payload, ensure_ascii=False)
            self._record_event(tool_name, call_num, mode, "Stripped schema:telephone and schema:email fields")
            return MCPToolExecutionResult(
                tool_name=tool_name,
                arguments=args,
                success=True,
                is_error=False,
                latency_ms=base_res.latency_ms,
                raw_text_outputs=[stripped_text],
                parsed_json_output=stripped_payload,
            )

        return await self._forward_or_mock_success(tool_name, args, timeout_seconds)

    def _should_inject_fault(self, tool_name: str, call_num: int, args: dict[str, Any]) -> bool:
        """Determine whether to inject a fault on this specific call."""
        if self.config.mode == FaultMode.NONE:
            return False
        if self.config.target_tools and tool_name not in self.config.target_tools:
            return False
        # Special case for SPARQL timeout recovery: if the agent retries with a bounded LIMIT <= 50, allow recovery
        if (
            self.config.mode == FaultMode.TIMEOUT_30S
            and tool_name == "sparql_query"
            and call_num > 1
            and "LIMIT" in str(args.get("query", "")).upper()
        ):
            return False
        return call_num <= self.config.fail_first_n_calls

    async def _forward_or_mock_success(
        self,
        tool_name: str,
        args: dict[str, Any],
        timeout_seconds: float | None,
    ) -> MCPToolExecutionResult:
        """Forward to downstream MCPBenchmarkClient if connected, or return a deterministic mock payload."""
        if self.downstream_client is not None:
            return await self.downstream_client.call_tool(
                tool_name=tool_name,
                arguments=args,
                timeout_seconds=timeout_seconds,
            )
        start_perf = time.perf_counter()
        mock_payload = [
            {
                "iri": "https://civic-data.de/federated-directory#org-00416a783a9b",
                "schema:name": "Drehscheibe Kinder - und Jugendpolitik",
                "schema:telephone": None,
                "schema:email": None,
                "schema:address": "https://civic-data.de/federated-directory#addr-4475eee6e4b7",
                "status": "sosuse:query:success",
            }
        ]
        latency_ms = (time.perf_counter() - start_perf) * 1000.0
        return MCPToolExecutionResult(
            tool_name=tool_name,
            arguments=args,
            success=True,
            is_error=False,
            latency_ms=round(latency_ms, 2),
            raw_text_outputs=[json.dumps(mock_payload, ensure_ascii=False)],
            parsed_json_output=mock_payload,
        )

    @classmethod
    def _strip_contact_fields(cls, payload: Any) -> Any:
        """Recursively set telephone/email fields to None to simulate the ~80% null contact trap."""
        contact_keys = {"schema:telephone", "schema:email", "telephone", "phone", "email"}
        if isinstance(payload, list):
            return [cls._strip_contact_fields(item) for item in payload]
        if isinstance(payload, dict):
            return {
                k: (None if k in contact_keys else cls._strip_contact_fields(v))
                for k, v in payload.items()
            }
        return payload

    def _record_event(self, tool_name: str, call_num: int, mode: FaultMode, detail: str) -> None:
        self.report.events.append(
            {
                "tool_name": tool_name,
                "call_number": call_num,
                "fault_mode": mode.value,
                "detail": detail,
            }
        )
