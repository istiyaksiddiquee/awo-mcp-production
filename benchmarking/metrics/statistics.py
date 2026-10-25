"""Statistical Confidence Intervals and Distribution Estimators for MCP Benchmarking.

Implements:
- Wilson Score 95% (or arbitrary confidence level) Binomial Confidence Intervals
  (robust for small-to-medium N and extreme pass rates near 0.0 or 1.0).
- Normal-approximation (Wald) Standard Error and Margin of Error matching the
  Excel `Model_Benchmark_Results` / `Permutation_Scale_Engine` formulas.
- Latency & TTFT percentile distributions (mean, std, p50, p90, p95, p99).
"""

from __future__ import annotations

import math
import statistics as stats_lib
from statistics import NormalDist
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field


class ConfidenceIntervalBound(BaseModel):
    """Structured Pydantic model for Wilson and Wald binomial confidence intervals."""

    model_config = ConfigDict(extra="forbid")

    n: int = Field(..., ge=0, description="Total number of evaluated trials / test cases")
    successes: float = Field(..., ge=0.0, description="Number of passing trials (or sum of continuous scores)")
    proportion: float = Field(..., ge=0.0, le=1.0, description="Observed pass rate / mean score p_hat")
    confidence_level: float = Field(default=0.95, gt=0.0, lt=1.0)
    z_score: float = Field(..., gt=0.0)
    wilson_center: float = Field(..., ge=0.0, le=1.0)
    wilson_lower: float = Field(..., ge=0.0, le=1.0)
    wilson_upper: float = Field(..., ge=0.0, le=1.0)
    wilson_half_width: float = Field(..., ge=0.0, le=1.0)
    wald_standard_error: float = Field(..., ge=0.0)
    wald_margin_of_error: float = Field(..., ge=0.0)


class LatencyDistributionStats(BaseModel):
    """Summary statistics for latency (ms) and token distributions."""

    model_config = ConfigDict(extra="forbid")

    count: int = 0
    mean_ms: float = 0.0
    std_ms: float = 0.0
    min_ms: float = 0.0
    p50_ms: float = 0.0
    p90_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    max_ms: float = 0.0


def compute_wilson_confidence_interval(
    successes: int | float,
    n: int,
    confidence_level: float = 0.95,
) -> ConfidenceIntervalBound:
    """Compute the Wilson score confidence interval for a binomial proportion (or bounded score in [0, 1]).

    Formula:
        center = (p + z^2 / (2n)) / (1 + z^2 / n)
        half_width = (z / (1 + z^2 / n)) * sqrt(p(1 - p) / n + z^2 / (4n^2))
    """
    alpha = 1.0 - confidence_level
    z = float(NormalDist().inv_cdf(1.0 - alpha / 2.0))

    if n <= 0:
        return ConfidenceIntervalBound(
            n=0,
            successes=0.0,
            proportion=0.0,
            confidence_level=confidence_level,
            z_score=round(z, 6),
            wilson_center=0.0,
            wilson_lower=0.0,
            wilson_upper=0.0,
            wilson_half_width=0.0,
            wald_standard_error=0.0,
            wald_margin_of_error=0.0,
        )

    clamped_successes = max(0.0, min(float(n), float(successes)))
    p_hat = clamped_successes / float(n)
    z2 = z * z
    denom = 1.0 + z2 / float(n)

    center = (p_hat + z2 / (2.0 * float(n))) / denom
    radicand = (p_hat * (1.0 - p_hat) / float(n)) + (z2 / (4.0 * float(n) * float(n)))
    half_width = (z / denom) * math.sqrt(max(0.0, radicand))

    lower = max(0.0, center - half_width)
    upper = min(1.0, center + half_width)

    wald_se = math.sqrt(max(0.0, p_hat * (1.0 - p_hat) / float(n)))
    wald_moe = z * wald_se

    return ConfidenceIntervalBound(
        n=n,
        successes=round(clamped_successes, 4),
        proportion=round(p_hat, 6),
        confidence_level=confidence_level,
        z_score=round(z, 6),
        wilson_center=round(center, 6),
        wilson_lower=round(lower, 6),
        wilson_upper=round(upper, 6),
        wilson_half_width=round(half_width, 6),
        wald_standard_error=round(wald_se, 6),
        wald_margin_of_error=round(wald_moe, 6),
    )


def compute_wald_confidence_interval(
    pass_rate_p: float,
    n: int,
    confidence_level: float = 0.95,
) -> ConfidenceIntervalBound:
    """Convenience wrapper computing both Wilson and Wald intervals directly from pass_rate `p` and sample size `n`."""
    successes = pass_rate_p * n
    return compute_wilson_confidence_interval(successes=successes, n=n, confidence_level=confidence_level)


def compute_latency_percentiles(latencies_ms: Sequence[float]) -> LatencyDistributionStats:
    """Compute mean, standard deviation, and p50/p90/p95/p99 percentiles for latency measurements."""
    clean = [float(x) for x in latencies_ms if x is not None and not math.isnan(float(x)) and float(x) >= 0.0]
    if not clean:
        return LatencyDistributionStats()

    ordered = sorted(clean)

    def percentile(p: float) -> float:
        """Linear interpolation between closest ranks (numpy's default)."""
        position = (len(ordered) - 1) * p / 100.0
        low, high = math.floor(position), math.ceil(position)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)

    return LatencyDistributionStats(
        count=len(clean),
        mean_ms=round(stats_lib.fmean(ordered), 2),
        std_ms=round(stats_lib.pstdev(ordered), 2),
        min_ms=round(ordered[0], 2),
        p50_ms=round(percentile(50), 2),
        p90_ms=round(percentile(90), 2),
        p95_ms=round(percentile(95), 2),
        p99_ms=round(percentile(99), 2),
        max_ms=round(ordered[-1], 2),
    )
