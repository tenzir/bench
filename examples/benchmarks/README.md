# Benchmark Pipelines

All runnable examples under this directory follow the same structure as the
real benchmark repositories:

- one benchmark directory per benchmark id
- one `bench.yaml` with benchmark-wide metadata
- one or more implementation `.tql` files with `bench:` frontmatter

For example:

```text
examples/benchmarks/suricata_parse_json/
├── bench.yaml
└── default.tql
```

## Benchmark manifest recap

`bench.yaml` carries the shared benchmark metadata:

```yaml
description: Import 100k records from disk
tags:
  dataset: suricata
  scenario: import
input:
  path: suricata/eve.json
  events: 984865
  measure: true
env:
  TENZIR_CONSOLE_FORMAT: none
fixtures:
  - kafka:
      topic: bench
runtime:
  warmup_runs: 1
  measurement_runs: 3
  timeout_seconds: 600
```

Each implementation file carries only implementation-specific metadata and the
pipeline body:

```yaml
---
bench:
  id: neo
  description: Import the dataset with --neo enabled.
  min_version: "5.30.0"
  tenzir_args:
    - --neo
---
```

The harness injects `BENCHMARK_INPUT_PATH` and, when `output.path` is defined,
`BENCHMARK_OUTPUT_PATH`. Paths are relative to the cached datasets produced by
`bench prepare`.

Fixtures follow the same declaration style as `tenzir/test`: use bare names for
fixtures without options, or a single-key mapping for structured options. The
singular alias `benchmark.fixture` is accepted for a single fixture. Fixture
definitions live in nearby `fixtures.py` modules and `fixtures/*.py`
directories, including nested per-fixture subdirectories, that are auto-loaded
from the current working directory down to the benchmark directory.

Fixtures stay active for the full benchmark and may expose `before_run` and
`after_run` hooks through `tenzir_bench.fixtures.FixtureHandle` to reset state
between warmup and measurement runs. This is the intended way to keep sources
like Kafka populated across repeated iterations.

New fixtures should use `current_context().runtime` instead of resolving
executables from `PATH`. The runtime already knows whether the benchmark is
running against a local binary or a `docker://...` target, and exposes
target-agnostic helpers for both `tenzir` and `tenzir-node`.

The `suricata_node_catalog_lookup` example shows this pattern. Its
`node_catalog_lookup` fixture:

- starts `tenzir-node` through `runtime.popen_tenzir_node(...)`
- seeds the node through `runtime.run_tenzir(...)`
- keeps the benchmark logic independent of the selected runtime target

## Example pipelines

This repository keeps only three runnable examples:

- `suricata_parse_json/` is a minimal fixtureless benchmark.
- `suricata_node_catalog_lookup/` uses the node fixture and the runtime API.
- `suricata_from_kafka/` uses the Kafka fixture from
  `examples/fixtures/kafka/fixture.py`.

## Adding New Benchmarks

1. Create `examples/benchmarks/<id>/` with a `bench.yaml` and one or more
   implementation `.tql` files.
1. Run `bench run path/to/<id>` or `bench run path/to/<id>/<impl>.tql` to
   generate measurement reports.
1. Validate results with `bench eval`.

Benchmarks should avoid absolute dataset references; rely on the cached dataset
layout instead so the harness remains portable across machines.
