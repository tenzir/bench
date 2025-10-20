# Tenzir Benchmark Harness

This repository hosts a portable benchmarking harness for the Tenzir data
pipeline engine. It focuses on repeatable measurement of realistic pipelines
and operators across Tenzir releases.

## Running Benchmarks

### `bench prepare`

Fetch the reference datasets (currently Suricata EVE JSON and Zeek conn logs),
derive helper artifacts such as CSV and key-value views, and store everything
under `data/`:

```bash
bench prepare
```

The command can be re-run; add `--force` to refresh downloads.

### `bench run`

Execute the benchmark suite or an individual pipeline. The command discovers
`.tql` files under `benchmarks/`, stages datasets from
`data`, and records per-run JSON reports in
`results/`.

```bash
bench run
```

Use `bench run path/to/pipeline.tql` to target a specific scenario.

### `bench sync`

Download the reference results from the central location (s3). By default,
downloads only the result data matching the current architecture. Only takes the
results generated from the most recently published main branch and all published
release versions of Tenzir.

Syncs all results when the `--full` flag is given.

### `bench eval`

Compare the latest runs against a set of reference runs.
By default, the benchmark results of the most recently published release version
are used as the baseline, and the latest published results of the main branch are
included for comparison.
The results of the reference runs are fetched from a central s3 bucket by default.
The compact output mode summarizes wall-clock time and peak RSS of the fastest
run per pipeline.

```bash
bench eval --base baseline/ --runs benchmark/results --compact
```

The optional `--base` option 

The full JSON report (without `--compact`) includes raw metrics, absolute
deltas, and percentage changes for every measured attribute.

When invoked without `--base`/`--runs`, `bench eval` automatically queries GitHub
for the most recent release tags and the latest commits on `main`, then selects
the corresponding artifacts that were previously synced via `bench sync`. It
uses the newest main-branch report available in the central location for the
current hardware. If no main results exist for this platform, `bench eval`
emits a warning and omits the main comparison. If neither release nor main
references are available, the command fails with an error message that suggests
running `bench compare` against locally built binaries instead. Use `--strict`
to make the command stop when references are missing or stale rather than
falling back to partial output.

### `bench publish`

Upload benchmark reports to the configured publication target (for example, an
object storage bucket). Publishing is idempotent: previously uploaded artifacts
are skipped unless `--force` is specified.

```bash
bench publish --runs benchmark/results --destination s3://tenzir-benchmarks/main
```

Credentials, bucket names, retention policies, and other publishing details are
managed via the project configuration (to be defined).

### `bench compare`

Runs the benchmarks for 2 Tenzir builds locally and compares the results directly.
Uses cached results in case the binary, the benchmark TQL file and the defined input
files did not change.

```bash
bench compare <path/to/baseline/bin/tenzir> <path/to/under-test/bin/tenzir> --compact
```

The compact output mode summarizes wall-clock time and peak RSS of the fastest
run per pipeline in a compact table.

You can also reference synced artefacts directly, e.g. `bench compare
--baseline latest-release --under-test main` to diff the cached release and main
results without locating binaries manually.

## Writing Benchmarks and Managing Baselines

### Authoring Pipelines

Each benchmark is a `.tql` file with YAML frontmatter followed by the pipeline
body. The harness injects `BENCHMARK_INPUT_PATH` and (when applicable)
`BENCHMARK_OUTPUT_PATH` so that pipelines can reference staged datasets without
hard-coded paths.

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
  input:                             # Input dataset configuration (required)
    path: suricata/eve.json          # Relative to $TENZIR_BENCHMARK_DATA unless absolute
    events: 984865                    # Optional record count for throughput stats
    measure: true                     # Use input bytes for throughput (boolean)
  output:                            # Optional output measurement settings
    path: tmp/eve-out.json           # Relative to working directory
    measure: false                   # Measure output bytes (input measure must be false)
  env:                               # Extra environment variables for the run (optional)
    TENZIR_CONSOLE_FORMAT: none
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

To add a new benchmark:

1. Create `benchmark/benchmarks/<category>/<id>.tql` with the required
   frontmatter and pipeline.
2. Run `bench run path/to/<id>.tql` to generate measurement reports.
3. Validate results with `bench eval --base … --runs …`.

Runners wrap the Tenzir invocation to collect metrics (e.g., `/usr/bin/time`
for wall clock/CPU/RSS, `perf` for hardware counters, `cachegrind` for cache
statistics).

### Building Baselines Across Releases
Maintaining baselines for all released Tenzir versions helps detect regressions
when new changes land. A typical workflow:

1. Enumerate the desired release binaries (for example, via Nix or container
   images).
2. For each release, invoke `bench run --tenzir-bin <path>` and store the JSON
   output under a dedicated baseline directory, e.g.
   `baseline/<release>/<benchmark-id>/<timestamp>.json`.
3. After all releases have been measured, aggregate the baseline directory into
   versioned artifacts (tarball, JSON archive) and upload them with
   `bench publish --runs baseline/<release> --destination …`.

Automating the loop is encouraged: a simple script can iterate over release
executables, set `TENZIR_BENCHMARK_DATA`, run the suite, and finally call
`bench publish` once per release or for the entire batch. Downstream evaluators
can then diff a development build against any published baseline.

Every published run should include metadata tying it back to the benchmark
definition revision (e.g., Git commit hash) so that `bench eval` and
`bench compare` can refuse to mix incompatible baselines.

## License

This project is licensed under the [Apache License, Version 2.0](LICENSE).
