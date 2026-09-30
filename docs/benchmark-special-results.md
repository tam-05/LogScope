# LogScope Benchmark Results

Measured against a running HTTP server. Every timed response was checked against
ground truth counted independently while generating the files.

Environment: `{"python": "3.14.6", "platform": "Windows-11-10.0.26200-SP0", "cpu_count": 4, "ram_bytes": 8320221184, "server_pids": [20808], "replica_count_configured": 1, "server_rss_processes_measured": 1, "dependency_versions": {"fastapi": "0.142.2", "uvicorn": "0.54.0", "httpx": "0.28.1", "anyio": "4.15.1"}, "latency_conditions": "warm loopback uploads, concurrency 1", "error_policy": "error_and_fatal", "service_cardinality": 10}`

| File | Bytes | Lines | Runs | p50 | p95 | Peak server RSS |
|---|---:|---:|---:|---:|---:|---:|
| bench_1mb.log | 1,048,561 | 15,837 | 1 | 0.168s | 0.168s | 60.2 MiB |

Health p95 during largest upload: 20.0 ms (10 probes).
Overload probe: 0 accepted, 4 busy responses.
Concurrency-eight probe: 1 accepted, 7 busy, 4.97 accepted MiB/s.

Special workloads (exact expected response verified): bench_long_line.log (65,571 bytes), bench_cardinality.log (360,041 bytes).

## Raw measurements

```json
{
  "environment": {
    "python": "3.14.6",
    "platform": "Windows-11-10.0.26200-SP0",
    "cpu_count": 4,
    "ram_bytes": 8320221184,
    "server_pids": [
      20808
    ],
    "replica_count_configured": 1,
    "server_rss_processes_measured": 1,
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
      "runs": 1,
      "p50_seconds": 0.16814560000784695,
      "p95_seconds": 0.16814560000784695,
      "peak_server_rss_bytes": 63164416,
      "correctness": "exact response verified on every run"
    }
  ],
  "health_ms": [
    9.210900054313242,
    17.96590001322329,
    20.043700002133846,
    7.844100007787347,
    7.166100083850324,
    3.7387999473139644,
    6.154200062155724,
    9.66460001654923,
    3.0979999573901296,
    6.6816998878493905
  ],
  "overload": {
    "accepted": 0,
    "busy": 4
  },
  "concurrent": {
    "concurrency": 8,
    "accepted": 1,
    "busy": 7,
    "elapsed_seconds": 0.20111860008910298,
    "accepted_mib_per_second": 4.972119408360157
  },
  "special_workloads": [
    {
      "file": "bench_long_line.log",
      "bytes": 65571,
      "seconds": 0.02145810006186366,
      "peak_server_rss_bytes": 64176128,
      "correctness": "exact response verified"
    },
    {
      "file": "bench_cardinality.log",
      "bytes": 360041,
      "seconds": 0.1368013999890536,
      "peak_server_rss_bytes": 65884160,
      "correctness": "exact response verified"
    }
  ]
}
```
