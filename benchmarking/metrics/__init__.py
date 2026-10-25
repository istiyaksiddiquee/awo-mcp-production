"""Scorecards, summaries with Wilson intervals, and paired tool-profile comparisons."""

from benchmarking.metrics.aggregator import (
    GroupSummary, PairedChange, RunScorecard, build_scorecard, paired_changes, summarize,
)
from benchmarking.metrics.statistics import (
    ConfidenceIntervalBound, LatencyDistributionStats, compute_latency_percentiles,
    compute_wilson_confidence_interval,
)

__all__ = [
    "ConfidenceIntervalBound",
    "GroupSummary",
    "LatencyDistributionStats",
    "PairedChange",
    "RunScorecard",
    "build_scorecard",
    "compute_latency_percentiles",
    "compute_wilson_confidence_interval",
    "paired_changes",
    "summarize",
]
