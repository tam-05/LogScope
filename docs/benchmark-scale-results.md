# LogScope Benchmark Results

Routing smoke check: after recreating `api-a` while Nginx stayed running,
20 authenticated sample uploads returned HTTP 200. This checks one restart;
the README documents the static-upstream address-change limitation.

Measured against a running HTTP server. Every timed response was checked against
ground truth counted independently while generating the files.

Environment: `{"python": "3.14.6", "platform": "Windows-11-10.0.26200-SP0", "cpu_count": 4, "ram_bytes": 8320221184, "server_pids": [], "replica_count_configured": 2, "server_rss_processes_measured": 0, "dependency_versions": {"fastapi": "0.142.2", "uvicorn": "0.54.0", "httpx": "0.28.1", "anyio": "4.15.1"}, "latency_conditions": "warm loopback uploads, concurrency 1", "error_policy": "error_and_fatal", "service_cardinality": 10}`

| File | Bytes | Lines | Runs | p50 | p95 | Peak server RSS |
|---|---:|---:|---:|---:|---:|---:|
| bench_1mb.log | 1,048,561 | 15,837 | 3 | 0.218s | 0.520s | unverified |

Health p95 during largest upload: 14.9 ms (10 probes).
Overload probe: 2 accepted, 2 busy responses.
Concurrency-eight probe: 2 accepted, 6 busy, 3.09 accepted MiB/s.

Special workloads (exact expected response verified): bench_long_line.log (65,571 bytes), bench_cardinality.log (360,041 bytes).

## Raw measurements

```json
{
  "environment": {
    "python": "3.14.6",
    "platform": "Windows-11-10.0.26200-SP0",
    "cpu_count": 4,
    "ram_bytes": 8320221184,
    "server_pids": [],
    "replica_count_configured": 2,
    "server_rss_processes_measured": 0,
    "dependency_versions": {
      "fastapi": "0.142.2",
      "uvicorn": "0.54.0",
      "httpx": "0.28.1",
      "anyio": "4.15.1"
    },
    "latency_conditions": "warm loopback uploads, concurrency 1",
    "error_policy": "error_and_fatal",
    "service_cardinality": 10
  },
  "results": [
    {
      "file": "bench_1mb.log",
      "actual_bytes": 1048561,
      "lines": 15837,
      "runs": 3,
      "p50_seconds": 0.21780610003042966,
      "p95_seconds": 0.520266699953936,
      "peak_server_rss_bytes": null,
      "correctness": "exact response verified on every run"
    }
  ],
  "health_ms": [
    14.925999916158617,
    6.858800072222948,
    9.05260001309216,
    9.467100026085973,
    8.638200000859797,
    6.992599926888943,
    6.124700070358813,
    7.876100018620491,
    8.046199916861951,
    6.891800090670586
  ],
  "overload": {
    "accepted": 2,
    "busy": 2
  },
  "concurrent": {
    "concurrency": 8,
    "accepted": 2,
    "busy": 6,
    "elapsed_seconds": 0.6482060999842361,
    "accepted_mib_per_second": 3.085394274782582
  },
  "special_workloads": [
    {
      "file": "bench_long_line.log",
      "bytes": 65571,
      "seconds": 0.03803549997974187,
      "peak_server_rss_bytes": null,
      "correctness": "exact response verified"
    },
    {
      "file": "bench_cardinality.log",
      "bytes": 360041,
      "seconds": 0.28612409997731447,
      "peak_server_rss_bytes": null,
      "correctness": "exact response verified"
    }
  ]
}
```
