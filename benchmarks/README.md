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

## Example Pipelines

- `operators/` contains benchmarks for core Tenzir operators (JSON/KV/CSV
  parsing, filtering, sorting, summarizing, deduplication).
- `examples/` hosts smaller micro-benchmarks for quick sanity checks.

## Adding New Benchmarks

1. Create a `.tql` file under an appropriate subdirectory with the required
   frontmatter and pipeline body.
2. Run `bench run path/to/benchmark.tql` to generate measurement reports.
3. Validate results with `bench eval`.

Benchmarks should avoid absolute dataset references; rely on the cached dataset
layout instead so the harness remains portable across machines.
