"""
Monotonic timing helpers.

Use now_ns() for ALL latency math, tick scheduling, and stage timestamps.
Use epoch_ns() ONLY to correlate timestamps across machines (pair with PTP/NTP sync).
"""
import time


def now_ns() -> int:
    """Monotonic high-resolution clock. Immune to wall-clock jumps."""
    return time.perf_counter_ns()


def epoch_ns() -> int:
    """Wall-clock nanoseconds. Use for cross-machine correlation only."""
    return time.time_ns()


def epoch_ms() -> float:
    """Wall-clock milliseconds as float. Convenient for DB timestamps."""
    return time.time() * 1_000.0
