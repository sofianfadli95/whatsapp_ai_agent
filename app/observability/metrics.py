"""Prometheus-style metrics for key operations.

Provides lightweight, thread-safe counters and histograms that can be
exposed via a `/metrics` endpoint later. No external service dependency
is required for these counters to function.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Generator


class Counter:
    """Thread-safe monotonic counter."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self._value: float = 0.0
        self._lock = threading.Lock()
        self._labels: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        """Increment the counter by the given amount."""
        if amount < 0:
            raise ValueError("Counter increment must be non-negative")
        with self._lock:
            if labels:
                key = tuple(sorted(labels.items()))
                self._labels[key] += amount
            else:
                self._value += amount

    @property
    def value(self) -> float:
        """Get the current counter value (unlabeled)."""
        with self._lock:
            return self._value

    def get(self, **labels: str) -> float:
        """Get the counter value for specific labels."""
        with self._lock:
            if labels:
                key = tuple(sorted(labels.items()))
                return self._labels[key]
            return self._value

    def collect(self) -> dict[str, float]:
        """Collect all counter values for exposition."""
        with self._lock:
            result: dict[str, float] = {}
            if self._value > 0:
                result[self.name] = self._value
            for label_set, val in self._labels.items():
                label_str = ",".join(f'{k}="{v}"' for k, v in label_set)
                result[f"{self.name}{{{label_str}}}"] = val
            return result


class Histogram:
    """Thread-safe histogram with configurable buckets."""

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(
        self, name: str, description: str = "", buckets: tuple[float, ...] | None = None
    ) -> None:
        self.name = name
        self.description = description
        self._buckets = buckets or self.DEFAULT_BUCKETS
        self._lock = threading.Lock()
        self._sum: float = 0.0
        self._count: int = 0
        self._bucket_counts: list[int] = [0] * len(self._buckets)

    def observe(self, value: float) -> None:
        """Record an observation."""
        with self._lock:
            self._sum += value
            self._count += 1
            for i, bound in enumerate(self._buckets):
                if value <= bound:
                    self._bucket_counts[i] += 1

    @contextmanager
    def time(self) -> Generator[None, None, None]:
        """Context manager to measure elapsed time and record it."""
        start = time.perf_counter()
        yield
        elapsed = time.perf_counter() - start
        self.observe(elapsed)

    @property
    def count(self) -> int:
        """Total number of observations."""
        with self._lock:
            return self._count

    @property
    def sum(self) -> float:
        """Sum of all observed values."""
        with self._lock:
            return self._sum

    def collect(self) -> dict[str, float]:
        """Collect histogram data for exposition."""
        with self._lock:
            result: dict[str, float] = {
                f"{self.name}_count": float(self._count),
                f"{self.name}_sum": self._sum,
            }
            for i, bound in enumerate(self._buckets):
                result[f"{self.name}_bucket{{le=\"{bound}\"}}"] = float(
                    self._bucket_counts[i]
                )
            return result


# ---------------------------------------------------------------------------
# Application-level metrics
# ---------------------------------------------------------------------------

# Message counters
inbound_messages_total = Counter(
    "inbound_messages_total",
    "Total inbound WhatsApp messages received",
)

outbound_messages_total = Counter(
    "outbound_messages_total",
    "Total outbound WhatsApp messages sent",
)

# Agent counters
agent_runs_total = Counter(
    "agent_runs_total",
    "Total agent graph invocations",
)

tool_calls_total = Counter(
    "tool_calls_total",
    "Total tool calls made by the agent",
)

# Error counters
errors_total = Counter(
    "errors_total",
    "Total errors by category",
)

escalations_total = Counter(
    "escalations_total",
    "Total conversations escalated to human support",
)

# Latency histograms
agent_turn_duration_seconds = Histogram(
    "agent_turn_duration_seconds",
    "Time taken for a single agent turn",
)

webhook_processing_duration_seconds = Histogram(
    "webhook_processing_duration_seconds",
    "Time taken to process a webhook request",
)


def collect_all() -> dict[str, float]:
    """Collect all metrics for exposition (e.g., via /metrics endpoint)."""
    result: dict[str, float] = {}
    for metric in [
        inbound_messages_total,
        outbound_messages_total,
        agent_runs_total,
        tool_calls_total,
        errors_total,
        escalations_total,
        agent_turn_duration_seconds,
        webhook_processing_duration_seconds,
    ]:
        result.update(metric.collect())
    return result
