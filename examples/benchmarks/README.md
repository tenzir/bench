# Benchmark Pipelines

All benchmarks live under this directory as `.tql` files with YAML frontmatter
followed by the pipeline body. The `bench run` command discovers these files,
stages the required datasets from the managed cache, and records per-run JSON
reports under the XDG state directory (e.g. `~/.local/state/tenzir-bench/results`).

## Frontmatter Recap

```yaml
---
benchmark:
  id: example-import
  description: Import 100k records from disk
  tags:
    dataset: suricata
    scenario: import
  min_version: "5.17.0"
  max_version: "6.0.0"
  input:
    path: suricata/eve.json      # resolved relative to the dataset cache
    events: 984865
    measure: true
  output:
    path: tmp/eve-out.json
    measure: false
  env:
    TENZIR_CONSOLE_FORMAT: none
  fixtures:
    - kafka:
        topic: bench
    - sink
  tenzir_args:
    - --verbosity
    - info
  runner: time
  runtime:
    warmup_runs: 1
    measurement_runs: 3
    timeout_seconds: 600
---
```

The harness injects `BENCHMARK_INPUT_PATH` and, when `output.path` is defined,
`BENCHMARK_OUTPUT_PATH`. Paths are relative to the cached datasets produced by
`bench prepare`.

Fixtures follow the same declaration style as `tenzir/test`: use bare names for
fixtures without options, or a single-key mapping for structured options. The
singular alias `benchmark.fixture` is accepted for a single fixture. Fixture
definitions live in nearby `fixtures.py` modules that are auto-loaded from the
current working directory down to the benchmark directory.

Fixtures stay active for the full benchmark and may expose `before_run` and
`after_run` hooks through `tenzir_bench.fixtures.FixtureHandle` to reset state
between warmup and measurement runs. This is the intended way to keep sources
like Kafka populated across repeated iterations.

## Example Pipelines

- `operators/` contains benchmarks for core Tenzir operators (JSON/KV/CSV
  parsing, filtering, sorting, summarizing, deduplication).
- `examples/` hosts smaller micro-benchmarks for quick sanity checks.
- `integrations/` contains fixture-backed benchmarks for external systems such
  as Kafka.

## Adding New Benchmarks

1. Create a `.tql` file under an appropriate subdirectory with the required
   frontmatter and pipeline body.
1. Run `bench run path/to/benchmark.tql` to generate measurement reports.
1. Validate results with `bench eval`.

Benchmarks should avoid absolute dataset references; rely on the cached dataset
layout instead so the harness remains portable across machines.
