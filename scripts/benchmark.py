#!/usr/bin/env python3
"""Measure streamed HTTP uploads against independent generated ground truth."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx
import psutil
from generate_logs import generate

CHUNK_BYTES = 65_536


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


async def file_bytes(path: Path) -> Any:
    with path.open("rb") as source:
        while chunk := await asyncio.to_thread(source.read, CHUNK_BYTES):
            yield chunk


async def sample_rss(processes: list[psutil.Process], stop: asyncio.Event) -> list[int]:
    samples: list[int] = []
    while not stop.is_set():
        samples.append(sum(process.memory_info().rss for process in processes))
        await asyncio.sleep(0.01)
    samples.append(sum(process.memory_info().rss for process in processes))
    return samples


async def upload(
    client: httpx.AsyncClient,
    url: str,
    path: Path,
    expected: dict[str, Any],
    processes: list[psutil.Process],
) -> tuple[float, int | None]:
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(path.stat().st_size),
    }
    stop = asyncio.Event()
    sampler = asyncio.create_task(sample_rss(processes, stop)) if processes else None
    start = time.monotonic()
    try:
        response = await client.post(url, content=file_bytes(path), headers=headers)
        elapsed = time.monotonic() - start
    finally:
        stop.set()
    samples = await sampler if sampler is not None else []
    if response.status_code != 200:
        raise RuntimeError(f"{path.name}: HTTP {response.status_code}: {response.text[:200]}")
    if response.json() != expected:
        raise AssertionError(f"{path.name}: response differs from independent ground truth")
    return elapsed, max(samples) if samples else None


async def health_during_upload(
    client: httpx.AsyncClient,
    server: str,
    path: Path,
    expected: dict[str, Any],
    processes: list[psutil.Process],
) -> list[float]:
    analysis = asyncio.create_task(
        upload(client, f"{server}/api/v1/analyses", path, expected, processes)
    )
    latencies: list[float] = []
    while not analysis.done() or len(latencies) < 10:
        start = time.monotonic()
        response = await client.get(f"{server}/health/live")
        latencies.append((time.monotonic() - start) * 1000)
        if response.status_code != 200:
            raise RuntimeError(f"Health probe returned HTTP {response.status_code}")
        await asyncio.sleep(0.05)
    await analysis
    return latencies


async def busy_probe(client: httpx.AsyncClient, server: str) -> dict[str, int]:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_body() -> Any:
        yield b"2026-09-18 10:00:00 INFO held msg\n"
        started.set()
        await release.wait()

    held = asyncio.create_task(
        client.post(
            f"{server}/api/v1/analyses",
            content=slow_body(),
            headers={"Content-Type": "application/octet-stream"},
        )
    )
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.sleep(0.05)
    try:
        responses = await asyncio.gather(
            *[
                client.post(
                    f"{server}/api/v1/analyses",
                    content=b"",
                    headers={"Content-Type": "application/octet-stream"},
                )
                for _ in range(4)
            ]
        )
    finally:
        release.set()
        await held
    return {
        "accepted": sum(response.status_code == 200 for response in responses),
        "busy": sum(response.status_code == 503 for response in responses),
    }


def special_workloads() -> list[tuple[Path, dict[str, Any]]]:
    """Construct expected results without using the parser under test."""
    long_path = Path("examples/bench_long_line.log")
    long_path.write_bytes(b"x" * 65_537 + b"\n" + b"2026-09-18 10:00:00 ERROR svc ok\n")
    long_expected = {
        "schema_version": "1.0",
        "total_lines": 2,
        "parsed_lines": 1,
        "unparseable_lines": 1,
        "total_errors": 1,
        "error_levels": ["ERROR", "FATAL"],
        "error_counts": {"svc": 1},
        "top_service": "svc",
        "top_services": ["svc"],
        "top_error_count": 1,
        "parse_error_counts": {"line_too_long": 1},
        "parse_error_samples": [{"line_number": 1, "reason": "line_too_long"}],
    }
    cardinality_path = Path("examples/bench_cardinality.log")
    service_counts = {f"svc{index:04d}": 0 for index in range(10_000)}
    service_counts["svc0000"] = 1
    with cardinality_path.open("wb") as destination:
        for service in service_counts:
            destination.write(f"2026-09-18 10:00:00 INFO {service} ok\n".encode())
        destination.write(b"2026-09-18 10:00:00 ERROR svc0000 failed\n")
    cardinality_expected = {
        "schema_version": "1.0",
        "total_lines": 10_001,
        "parsed_lines": 10_001,
        "unparseable_lines": 0,
        "total_errors": 1,
        "error_levels": ["ERROR", "FATAL"],
        "error_counts": service_counts,
        "top_service": "svc0000",
        "top_services": ["svc0000"],
        "top_error_count": 1,
        "parse_error_counts": {},
        "parse_error_samples": [],
    }
    return [(long_path, long_expected), (cardinality_path, cardinality_expected)]


async def concurrent_probe(
    client: httpx.AsyncClient, server: str, path: Path, expected: dict[str, Any]
) -> dict[str, float | int]:
    """Measure accepted throughput and busy rate at concurrency eight."""
    url = f"{server}/api/v1/analyses"
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(path.stat().st_size),
    }
    start = time.monotonic()
    responses = await asyncio.gather(
        *[client.post(url, content=file_bytes(path), headers=headers) for _ in range(8)]
    )
    elapsed = time.monotonic() - start
    accepted = [response for response in responses if response.status_code == 200]
    if any(response.json() != expected for response in accepted):
        raise AssertionError("Concurrent response differs from expected counts")
    if any(response.status_code not in (200, 503) for response in responses):
        raise RuntimeError("Unexpected status during concurrent probe")
    return {
        "concurrency": 8,
        "accepted": len(accepted),
        "busy": len(responses) - len(accepted),
        "elapsed_seconds": elapsed,
        "accepted_mib_per_second": len(accepted) * path.stat().st_size / 1024**2 / elapsed,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--sizes", default="1,10,100", help="Comma-separated MiB sizes")
    parser.add_argument("--runs", type=int, default=None, help="Override runs at each size")
    parser.add_argument("--server-pid", type=int, action="append", default=[])
    parser.add_argument("--replicas", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("docs/benchmark-results.md"))
    args = parser.parse_args()
    sizes = [int(value) for value in args.sizes.split(",")]
    if not sizes or any(size <= 0 for size in sizes):
        parser.error("sizes must be positive integers")
    if args.runs is not None and args.runs <= 0:
        parser.error("runs must be positive")
    if args.replicas <= 0:
        parser.error("replicas must be positive")

    processes = [psutil.Process(pid) for pid in args.server_pid]
    token = os.environ.get("LOGSCOPE_API_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    results: list[dict[str, Any]] = []
    special_results: list[dict[str, Any]] = []
    generated: list[tuple[Path, dict[str, Any]]] = []
    for size in sizes:
        path = Path(f"examples/bench_{size}mb.log")
        truth = generate(size * 1024 * 1024, seed=42, output=path)
        expected = {key: value for key, value in truth.items() if not key.startswith("actual_")}
        generated.append((path, expected))
        print(f"Generated {path}: {truth['actual_bytes']:,} bytes, {truth['actual_lines']:,} lines")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(150.0), headers=headers, follow_redirects=False
    ) as client:
        response = await client.get(f"{args.server}/health/live")
        if response.status_code != 200:
            raise RuntimeError(f"Server health returned HTTP {response.status_code}")
        for path, expected in generated:
            size = round(path.stat().st_size / 1024**2)
            runs = args.runs if args.runs is not None else (30 if size == 10 else 1)
            await upload(client, f"{args.server}/api/v1/analyses", path, expected, processes)
            latencies: list[float] = []
            rss_peaks: list[int] = []
            for index in range(runs):
                elapsed, rss = await upload(
                    client, f"{args.server}/api/v1/analyses", path, expected, processes
                )
                latencies.append(elapsed)
                if rss is not None:
                    rss_peaks.append(rss)
                print(f"{path.name} run {index + 1}/{runs}: {elapsed:.3f}s")
            results.append(
                {
                    "file": path.name,
                    "actual_bytes": path.stat().st_size,
                    "lines": expected["total_lines"],
                    "runs": runs,
                    "p50_seconds": percentile(latencies, 0.5),
                    "p95_seconds": percentile(latencies, 0.95),
                    "peak_server_rss_bytes": max(rss_peaks) if rss_peaks else None,
                    "correctness": "exact response verified on every run",
                }
            )

        largest_path, largest_expected = generated[-1]
        health = await health_during_upload(
            client, args.server, largest_path, largest_expected, processes
        )
        busy = await busy_probe(client, args.server)
        concurrent = await concurrent_probe(client, args.server, generated[0][0], generated[0][1])
        for path, expected in special_workloads():
            elapsed, rss = await upload(
                client, f"{args.server}/api/v1/analyses", path, expected, processes
            )
            special_results.append(
                {
                    "file": path.name,
                    "bytes": path.stat().st_size,
                    "seconds": elapsed,
                    "peak_server_rss_bytes": rss,
                    "correctness": "exact response verified",
                }
            )

    environment = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu_count": psutil.cpu_count(logical=True),
        "ram_bytes": psutil.virtual_memory().total,
        "server_pids": args.server_pid,
        "replica_count_configured": args.replicas,
        "server_rss_processes_measured": len(args.server_pid),
        "dependency_versions": {
            name: version(name) for name in ("fastapi", "uvicorn", "httpx", "anyio")
        },
        "latency_conditions": "warm loopback uploads, concurrency 1",
        "error_policy": "error_and_fatal",
        "service_cardinality": 10,
    }
    report = [
        "# LogScope Benchmark Results",
        "",
        "Measured against a running HTTP server. Every timed response was checked against",
        "ground truth counted independently while generating the files.",
        "",
        f"Environment: `{json.dumps(environment)}`",
        "",
        "| File | Bytes | Lines | Runs | p50 | p95 | Peak server RSS |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for result in results:
        rss = result["peak_server_rss_bytes"]
        rss_text = f"{rss / 1024**2:.1f} MiB" if rss is not None else "unverified"
        report.append(
            f"| {result['file']} | {result['actual_bytes']:,} | {result['lines']:,} | "
            f"{result['runs']} | {result['p50_seconds']:.3f}s | "
            f"{result['p95_seconds']:.3f}s | {rss_text} |"
        )
    report.extend(
        [
            "",
            f"Health p95 during largest upload: {percentile(health, 0.95):.1f} ms "
            f"({len(health)} probes).",
            f"Overload probe: {busy['accepted']} accepted, {busy['busy']} busy responses.",
            f"Concurrency-eight probe: {concurrent['accepted']} accepted, "
            f"{concurrent['busy']} busy, "
            f"{concurrent['accepted_mib_per_second']:.2f} accepted MiB/s.",
            "",
            "Special workloads (exact expected response verified): "
            + ", ".join(
                f"{result['file']} ({result['bytes']:,} bytes)" for result in special_results
            )
            + ".",
            "",
            "## Raw measurements",
            "",
            "```json",
            json.dumps(
                {
                    "environment": environment,
                    "results": results,
                    "health_ms": health,
                    "overload": busy,
                    "concurrent": concurrent,
                    "special_workloads": special_results,
                },
                indent=2,
            ),
            "```",
            "",
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(report), encoding="utf-8")
    print(f"Report written to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
