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
inputs:
  main:
    path: suricata/eve.json
    repetitions: 1
    source:
      num_events: 984865
env:
  TENZIR_CONSOLE_FORMAT: none
fixtures:
  - kafka:
      inputs: [main]
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

## Variants

A variant runs the same implementation file again with different Tenzir
arguments. Use variants when the pipeline body is identical and only the
engine configuration changes, such as a parallelism degree:

```yaml
---
bench:
  id: neo
  tenzir_args:
    - --neo
  variants:
    p1:
      tenzir_args: ["--parallelism", "1"]
    p8:
      description: Parallelism degree 8.
      tenzir_args: ["--parallelism", "8"]
      tags:
        degree: "8"
---
```

Each variant becomes its own definition with the implementation id
`<implementation>/<variant>`, for example `parallel_cpu_bound/neo/p8`. Variant
`tenzir_args` append to the implementation's arguments, while `env` and `tags`
merge over the shared values. A variant may also override `description`,
`min_version`, and `max_version`.

Declaring `variants:` in `bench.yaml` applies them to every implementation of
the benchmark. An implementation that declares its own `variants:` overrides
the shared set.

Select variants on the command line with `--variant`, which accepts globs and
can be repeated. Implementations without variants always run:

```sh
tenzir-bench run --tenzir ./build/bin/tenzir --variant p1
tenzir-bench compare --base ./base/bin/tenzir --candidate ./cand/bin/tenzir \
  --variant 'p*' bench/benchmarks/parallel_cpu_bound
```

The harness injects one environment variable per named input in the form
`BENCHMARK_INPUT_<INPUT_NAME>_PATH`. Single-input benchmarks also get the
compatibility alias `BENCHMARK_INPUT_PATH`. When `output.path` is defined, the
harness also injects `BENCHMARK_OUTPUT_PATH`. Paths are relative to the cached
datasets produced by `bench prepare`.

Fixtures follow the same declaration style as `tenzir/test`: use bare names for
fixtures without options, or a single-key mapping for structured options. The
singular alias `benchmark.fixture` is accepted for a single fixture. Fixture
definitions live in nearby `fixtures.py` modules and `fixtures/*.py`
directories, including nested per-fixture subdirectories, that are auto-loaded
from the current working directory down to the benchmark directory.

Fixtures stay active for the full benchmark and may expose `seed`,
`before_run`, and `after_run` hooks through `tenzir_bench.fixtures.FixtureHandle`.
Each named input now owns its own repetition and source metadata. Fixture
`seed` hooks receive the selected staged inputs as a name-to-path mapping and
can load them into Kafka, nodes, or other external systems in a target-specific
way. Fixture specs may optionally declare `inputs: [...]` to restrict which
named benchmark inputs they receive.

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
