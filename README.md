# Tenzir Benchmark Harness

This repository hosts a portable benchmarking harness for the Tenzir data
pipeline engine. It focuses on repeatable measurement of realistic pipelines
and operators across Tenzir releases.

## Running Benchmarks

### `bench prepare`

Fetch the reference datasets (currently Suricata EVE JSON and Zeek conn logs),
derive helper artifacts such as CSV and key-value views, and store everything in
the platform-specific cache directory (e.g. `~/.cache/tenzir-bench/datasets`):

```bash
bench prepare
```

The command can be re-run; add `--force` to refresh downloads.

### `tenzir-bench run`

Execute the benchmark suite or selected spec benchmarks. After publishing to
PyPI, the intended UX is to run the harness directly with `uvx`:

```bash
uvx tenzir-bench run --tenzir-bin /path/to/tenzir --benchmark from_kafka_route53
```

The command auto-detects the repo root by looking for a checkout that contains
`bench/`, starting from the current directory and then from the `--tenzir-bin`
path. It discovers benchmarks under `bench/`, automatically stages any
repo-local `input.source.url` files into the cache, activates declared fixtures,
and records per-run JSON reports under the state directory (for example,
`~/.local/state/tenzir-bench/results`).

To run the default set from the repo root:

```bash
uvx tenzir-bench run --tenzir-bin ./build/bin/tenzir
```

To run a subset:

```bash
uvx tenzir-bench run --tenzir-bin /path/to/tenzir --benchmark from_kafka_route53
uvx tenzir-bench run --tenzir-bin /path/to/tenzir --benchmark 'from_kafka_*'
```

When developing locally before publishing, use:

```bash
uvx --from /path/to/bench tenzir-bench run --tenzir-bin /path/to/tenzir --benchmark from_kafka_route53
```

Use `--dry-run --verbose` to print the resolved invocation without probing the
Tenzir binary or starting fixtures. Use `--validate` when you want the full
validation behavior.

### `bench sync`

Download the reference results from the central location (s3). By default, the
command only downloads artifacts for all published release versions and the most
recent published `main` build that match the known artifact identifiers from
GitHub metadata.

Syncs all results when the `--full` flag is given.

Result artifacts are stored and synced using a deterministic layout derived from
the benchmark definition hash, input hash, runner, and Tenzir build identifier.
This keeps the local cache (`~/.cache/tenzir-bench/results`) aligned with the
remote bucket structure and prevents redundant downloads.

Metadata fetched from GitHub (release list and recent `main` commits) is cached
for 30 minutes; pass `--refresh` to bypass the TTL and force an update.

### `bench eval`

Compare the latest runs against a set of reference runs.
By default, the benchmark results of the most recently published release version
are used as the baseline, and the latest published results of the main branch are
included for comparison.
The results of the reference runs are fetched from a central s3 bucket by default.
The compact output mode summarizes wall-clock time and peak RSS of the fastest
run per pipeline.

```bash
bench eval --runs ~/.local/state/tenzir-bench/results --compact
```

The optional `--base` option points evaluation at an explicit baseline
directory instead of the synced release artifacts.

The full JSON report (without `--compact`) includes raw metrics, absolute
deltas, and percentage changes for every measured attribute.

When invoked without `--base`/`--runs`, `bench eval` automatically queries
GitHub for the most recent release tags and the latest commit on `main`, then
selects the corresponding artifacts that were previously synced via
`bench sync`. If no main results exist, `bench eval` emits a warning and omits
the main comparison. If neither release nor main references are available, the
command fails with an error message that suggests running `bench compare`
against locally built binaries instead.

> **Note:** GitHub API calls honor the `GITHUB_TOKEN` environment variable when
> present but also work unauthenticated (subject to rate limits).

### `bench publish`

Upload benchmark reports to the configured publication target (for example, an
object storage bucket). Publishing is idempotent: previously uploaded artifacts
are skipped unless `--force` is specified.

```bash
bench publish --runs ~/.local/state/tenzir-bench/results --destination s3://tenzir-benchmarks/main
```

Credentials, bucket names, retention policies, and other publishing details are
managed via the built-in defaults; authentication relies on the standard AWS
CLI configuration in the current environment.

### `bench compare`

Runs the benchmarks for 2 Tenzir builds locally and compares the results directly.
Uses cached results in case the binary, the benchmark TQL file and the defined input
files did not change.

```bash
bench compare <path/to/baseline/bin/tenzir> <path/to/under-test/bin/tenzir> --compact
```

The compact output mode summarizes wall-clock time and peak RSS of the fastest
run per pipeline in a compact table. Without `--compact`, the command prints a
more detailed view that also shows the staged report source paths.

Use `--dry-run --verbose` to print the resolved invocations for each build
without starting Tenzir or any benchmark fixtures.

For quick experiments you can point to Docker images as build candidates by
prefixing them with `docker://`, e.g.

```bash
bench compare --base docker://ghcr.io/tenzir/tenzir:v5.18.0 --candidate docker://ghcr.io/tenzir/tenzir:main examples/benchmarks/operators
```

The harness automatically mounts the dataset and state directories into the
container and streams the benchmark environment variables across.

## Writing Benchmarks and Managing Baselines

### Authoring Pipelines

Each benchmark is a `.tql` file with YAML frontmatter followed by the pipeline
body. The harness injects one environment variable per named input in the form
`BENCHMARK_INPUT_<INPUT_NAME>_PATH`, plus the single-input compatibility alias
`BENCHMARK_INPUT_PATH` when a benchmark defines only one input. It also injects
`BENCHMARK_OUTPUT_PATH` when `output.path` is configured. Pipelines can
reference staged datasets without hard-coded paths, and benchmarks can request
Python fixtures that provision external systems or seed data for
integration-style benchmarks such as `from_kafka`.

Frontmatter schema:

```
---
benchmark:
  id: string                         # Globally unique identifier (required)
  description: string                # Short human-readable summary (optional)
  tags:                              # Arbitrary key/value metadata (optional)
    dataset: suricata-eve
    operator: read_json
  min_version: "5.17.0"              # Minimum Tenzir version allowed (optional)
  max_version: "6.0.0"               # Maximum Tenzir version allowed (optional)
  inputs:                            # Named input dataset configurations (required)
    main:
      path: suricata/eve.json        # Relative to the managed dataset cache unless absolute
      repetitions: 1                 # Optional repetition count applied before fixture seeding
      source:                        # Optional source metadata for staging and throughput stats
        url: ../../test/tests/...    # Optional repo-local path or HTTP(S) URL copied into the cache automatically
        num_events: 984865           # Optional record count for one source copy
  output:                            # Optional output measurement settings
    path: tmp/eve-out.json           # Relative to working directory
  env:                               # Extra environment variables for the run (optional)
    TENZIR_CONSOLE_FORMAT: none
  fixtures:                          # Optional fixture specs (same shapes as tenzir/test)
    - kafka:
        inputs: [main]
        topic: bench
        partitions: 1
    - sink
  tenzir_args:                       # Extra CLI flags for the Tenzir binary (optional)
    - --verbosity
    - info
  runner: time                       # Measurement runner (optional; defaults to 'time')
  runtime:                           # Execution policy (optional)
    warmup_runs: 1                   # Warm-up iterations (default: 0)
    measurement_runs: 3              # Timed runs (default: 1)
    timeout_seconds: 600             # Per-run timeout (optional)
---
```

`benchmark.fixture: kafka` is accepted as a shorthand for a single fixture, but
`fixture` and `fixtures` cannot be used together.

Fixture modules are discovered from `fixtures.py` files and `fixtures/**/*.py`
modules between the current working directory and the benchmark file. An
example benchmark under `examples/benchmarks/suricata_from_kafka/` can
therefore use `examples/fixtures/kafka/fixture.py`.

Fixture modules use the `tenzir_bench.fixtures` API:

```python
from dataclasses import dataclass

from tenzir_bench.fixtures import FixtureHandle, current_options, fixture


@dataclass(frozen=True)
class KafkaOptions:
    topic: str = "bench"


@fixture(name="kafka", options=KafkaOptions)
def kafka_fixture():
    options = current_options("kafka")

    def seed(*, input_paths, **_kwargs):
        # Load one or more staged benchmark inputs into Kafka once per benchmark run.
        ...

    return FixtureHandle(
        env={"KAFKA_TOPIC": options.topic},
        hooks={"seed": seed},
    )
```

Fixtures stay active for the full benchmark execution and may expose
`seed`, `before_run`, and `after_run` hooks. The `seed` hook is the place to
load staged named inputs into Kafka, nodes, or other external systems. A
fixture may optionally declare `inputs: [...]` to receive only a subset of the
named benchmark inputs. Each input’s `repetitions` setting controls how many
times that staged input is repeated before any fixture seeding happens.
Fixture-provided environment variables are merged into the benchmark
environment and forwarded automatically when the benchmark runs inside a Docker
wrapper via `bench compare`.

This repository now ships three runnable examples under `examples/`:

- `examples/benchmarks/suricata_parse_json/`
- `examples/benchmarks/suricata_from_kafka/`
- `examples/benchmarks/suricata_node_catalog_lookup/`

To add a new benchmark:

1. Create `bench/benchmarks/<id>/` in the target repository for real benchmarks,
   or add a runnable sample under `examples/benchmarks/<id>/` in
   this repository.
1. Run `bench run path/to/<id>` to generate measurement reports.
1. Validate results with `bench eval`.

Runners wrap the Tenzir invocation to collect metrics (e.g., `/usr/bin/time`
for wall clock/CPU/RSS, `perf` for hardware counters, `cachegrind` for cache
statistics).

### Building Baselines Across Releases

Maintaining baselines for all released Tenzir versions helps detect regressions
when new changes land. A typical workflow:

1. Enumerate the desired release binaries (for example, via Nix or container
   images).
1. For each release, invoke `bench run --tenzir-bin <path>`. Reports are stored
   automatically in the state directory following the canonical
   `<benchmark-hash>/<input-hash>/<build-id>` layout.
1. After all releases have been measured, publish the collected results with
   `bench publish --runs ~/.local/state/tenzir-bench/results --destination …` or
   archive the directory as needed.

Automating the loop is encouraged: a simple script can iterate over release
executables, run the suite, and finally call `bench publish` once per release or
for the entire batch. Downstream evaluators
can then diff a development build against any published baseline.

Every published run should include metadata tying it back to the benchmark
definition revision (e.g., Git commit hash) so that `bench eval` and
`bench compare` can refuse to mix incompatible baselines.

## License

This project is licensed under the [Apache License, Version 2.0](LICENSE).
