#!/usr/bin/env python3
"""ChefChat Performance Benchmarks
===================================

Measure the performance of critical ChefChat operations:
- Mode switching speed
- Tool gatekeeper decision speed
- Config loading time

Usage:
    python scripts/benchmark.py

Target: < 1ms per operation for mode switches and gatekeeper checks
"""

from __future__ import annotations

from collections.abc import Callable
from statistics import mean, stdev
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def benchmark(
    func: Callable[[], None], iterations: int = 1000, name: str = "Operation"
) -> dict[str, float]:
    """Run a benchmark and return timing statistics.

    Args:
        func: Function to benchmark (should be fast, no I/O)
        iterations: Number of iterations to run
        name: Display name for the benchmark

    Returns:
        Dict with mean, min, max, stdev in milliseconds
    """
    times: list[float] = []

    # Warm-up
    for _ in range(10):
        func()

    # Actual benchmark
    for _ in range(iterations):
        start = time.perf_counter()
        func()
        end = time.perf_counter()
        times.append((end - start) * 1000)  # Convert to ms

    return {
        "name": name,
        "iterations": iterations,
        "mean_ms": mean(times),
        "min_ms": min(times),
        "max_ms": max(times),
        "stdev_ms": stdev(times) if len(times) > 1 else 0,
    }


def benchmark_mode_switch() -> dict[str, float]:
    """Measure mode switching speed."""
    from vibe.cli.mode_manager import ModeManager, VibeMode

    mm = ModeManager(initial_mode=VibeMode.NORMAL)

    def switch() -> None:
        mm.cycle_mode()

    return benchmark(switch, iterations=1000, name="Mode Cycle")


def benchmark_mode_set() -> dict[str, float]:
    """Measure direct mode setting speed."""
    from vibe.cli.mode_manager import ModeManager, VibeMode

    mm = ModeManager(initial_mode=VibeMode.NORMAL)
    modes = list(VibeMode)
    idx = [0]

    def set_mode() -> None:
        mm.set_mode(modes[idx[0] % len(modes)])
        idx[0] += 1

    return benchmark(set_mode, iterations=1000, name="Mode Set")


def benchmark_tool_approval() -> dict[str, float]:
    """Measure tool approval decision speed."""
    from vibe.cli.mode_manager import ModeManager, VibeMode

    mm = ModeManager(initial_mode=VibeMode.PLAN)

    def check() -> None:
        mm.should_approve_tool("write_file")

    return benchmark(check, iterations=1000, name="Tool Approval Check")


def benchmark_write_detection() -> dict[str, float]:
    """Measure write operation detection speed."""
    from vibe.cli.mode_manager import ModeManager, VibeMode

    mm = ModeManager(initial_mode=VibeMode.PLAN)
    args = {"command": "rm -rf /tmp/test"}

    def check() -> None:
        mm.is_write_operation("bash", args)

    return benchmark(check, iterations=1000, name="Write Detection (bash)")


def benchmark_system_prompt_modifier() -> dict[str, float]:
    """Measure system prompt modifier generation speed."""
    from vibe.cli.mode_manager import ModeManager, VibeMode

    mm = ModeManager(initial_mode=VibeMode.YOLO)

    def generate() -> None:
        _ = mm.get_system_prompt_modifier()

    return benchmark(generate, iterations=500, name="System Prompt Modifier")


def benchmark_config_load() -> dict[str, float]:
    """Measure config loading speed (includes file I/O)."""
    from vibe.core.config import VibeConfig, clear_config_cache

    def load() -> None:
        clear_config_cache()
        VibeConfig.load()

    # Fewer iterations since this involves file I/O
    return benchmark(load, iterations=50, name="Config Load (with I/O)")


def benchmark_config_cached() -> dict[str, float]:
    """Measure cached config access speed."""
    from vibe.core.config import get_config

    # Pre-load
    get_config()

    def get_cached() -> None:
        get_config()

    return benchmark(get_cached, iterations=1000, name="Config Get (cached)")


def main() -> None:
    """Run all benchmarks and display results."""
    console.print()
    console.print(
        Panel(
            "🏎️ ChefChat Performance Benchmarks",
            subtitle="Target: < 1ms per operation",
            border_style="#FF7000",
        )
    )
    console.print()

    # Run benchmarks (skip config ones if no API key)
    results = [
        benchmark_mode_switch(),
        benchmark_mode_set(),
        benchmark_tool_approval(),
        benchmark_write_detection(),
        benchmark_system_prompt_modifier(),
    ]

    # Try config benchmarks (may fail without API key)
    try:
        results.append(benchmark_config_cached())
        results.append(benchmark_config_load())
    except Exception as e:
        console.print(f"[yellow]⚠ Skipping config benchmarks: {e}[/yellow]")
        console.print()

    # Create results table
    table = Table(title="📊 Benchmark Results", show_header=True)
    table.add_column("Operation", style="cyan")
    table.add_column("Iterations", justify="right")
    table.add_column("Mean (ms)", justify="right")
    table.add_column("Min (ms)", justify="right")
    table.add_column("Max (ms)", justify="right")
    table.add_column("Status", justify="center")

    for result in results:
        mean_ms = result["mean_ms"]

        # Determine status
        if mean_ms < 0.1:
            status = "[green]✓ Excellent[/green]"
        elif mean_ms < 1.0:
            status = "[green]✓ Good[/green]"
        elif mean_ms < 10.0:
            status = "[yellow]⚠ OK[/yellow]"
        else:
            status = "[red]✗ Slow[/red]"

        table.add_row(
            result["name"],
            str(result["iterations"]),
            f"{mean_ms:.4f}",
            f"{result['min_ms']:.4f}",
            f"{result['max_ms']:.4f}",
            status,
        )

    console.print(table)
    console.print()

    # Summary
    all_fast = all(r["mean_ms"] < 1.0 for r in results if "I/O" not in r["name"])
    if all_fast:
        console.print("[green]✓ All core operations are under 1ms target![/green]")
    else:
        console.print("[yellow]⚠ Some operations exceed 1ms target[/yellow]")

    console.print()


if __name__ == "__main__":
    main()
