# tenzir-bench documentation

This guide describes the `tenzir-bench` command-line workflows. For benchmark
manifests, implementation files, and fixtures, read the [benchmark authoring
guide](../examples/benchmarks/README.md).

## Prepare datasets

Download the reference datasets and derive reusable artifacts such as CSV and
key-value views:

```sh
tenzir-bench prepare
```

The command stores datasets in the platform-specific cache directory, such as
`~/.cache/tenzir-bench/datasets` on Linux. You can run it repeatedly without
re-downloading existing data. Pass `--force` to refresh all datasets.

Repository-local files referenced by `inputs.<name>.source.url` are staged
automatically when you run a benchmark.

## Run benchmarks

Run the default benchmark set against a local Tenzir binary:

```sh
tenzir-bench run --tenzir ./build/bin/tenzir
```

Without a selection, `tenzir-bench` runs the benchmarks matching the glob
patterns in `bench/defaults.txt`, or all benchmarks if that file is missing or
empty. It skips benchmarks matching the patterns in `bench/excluded.txt`. Both
files list one benchmark ID glob per line and ignore blank lines and lines
starting with `#`. Explicit selections ignore both files.

Select benchmarks by an ID, a glob, a file, or a directory. Repeat
`--benchmark` to combine selections:

```sh
tenzir-bench run \
  --tenzir /path/to/tenzir \
  --benchmark suricata_parse_json \
  --benchmark 'from_kafka_*'
```

You can also use `--filter` to select benchmark IDs with a glob. The
`--filter` and `--benchmark` options are mutually exclusive.

Use a Docker image instead of a local executable by prefixing the target with
`docker://`:

```sh
tenzir-bench run \
  --tenzir docker://ghcr.io/tenzir/tenzir:main \
  --benchmark examples/benchmarks
```

The harness mounts the dataset and state directories into the container and
forwards the benchmark environment variables.

Use `--dry-run --verbose` to print resolved invocations without probing the
Tenzir target or starting fixtures. Use `--validate` to validate invocations
without measuring them.

Each run writes a JSON report to the platform-specific state directory, such
as `~/.local/state/tenzir-bench/results` on Linux. Reports use a deterministic
layout derived from the benchmark definition, inputs, runner, and Tenzir build.
This layout prevents incompatible or redundant results from being mixed.

### Run benchmarks in a deployment

By default, the harness runs each benchmark as a `tenzir` process and measures
that process. A benchmark that selects the `deployment` runner runs in a
deployment instead, which is how Tenzir v7 and later run pipelines in
production:

```yaml
---
bench:
  id: deployment
  min_version: "7.0.0"
  runner: deployment
---
```

For every warmup and measurement run, the harness does the following:

1. Start a deployment that has no connection to a platform and keeps its state
   in a temporary directory.
1. Hand the pipeline to the deployment through the deployment API.
1. Wait until the deployment reports that the pipeline finished.
1. Stop the deployment and remove the temporary directory.

The measurements differ from the ones of the default runner as follows:

- The wall-clock time spans from handing over the pipeline until the
  deployment reports that the pipeline finished. It excludes the time to start
  and stop the deployment.
- The CPU time and the peak resident memory cover the deployment process from
  its start to its exit.

Because of these differences, compare a deployment result only with other
deployment results.

A pipeline that runs in a deployment must meet these requirements:

- The pipeline starts with a source and ends with a sink, such as `discard`. A
  deployment has no standard input or output.
- The pipeline ends on its own. The run fails if the pipeline doesn't finish
  within `runtime.timeout_seconds`.
- The pipeline uses only operators that a deployment provides. For example, a
  deployment has no `import` and `export` operators.
- The implementation defines no `tenzir_args`, and you pass no Tenzir options
  on the command line. A deployment takes its engine settings from the
  environment.

The deployment starts with the benchmark environment, so `env("...")` in a
pipeline resolves inputs and fixture values as with the default runner.

To run a single pipeline this way without a benchmark definition, use the
module that the runner invokes. It prints the measurements as JSON:

```sh
python -m tenzir_bench.deployment \
  --tenzir /path/to/tenzir \
  --file pipeline.tql
```

### Run a development checkout

Before publishing a release, use `uvx --from` to execute the checkout:

```sh
uvx --from /path/to/bench tenzir-bench run \
  --tenzir /path/to/tenzir \
  --benchmark examples/benchmarks
```

## Compare builds

Run the same benchmarks against a baseline and one or more candidate builds:

```sh
tenzir-bench compare \
  --base /path/to/baseline/bin/tenzir \
  --candidate /path/to/candidate/bin/tenzir \
  examples/benchmarks \
  --compact
```

The command reuses compatible cached reports. Add `--run` immediately before a
build marker to force a new run for that build:

```sh
tenzir-bench compare \
  --base /path/to/baseline/bin/tenzir \
  --run --candidate /path/to/candidate/bin/tenzir \
  examples/benchmarks
```

Build markers can point to local binaries or Docker images:

```sh
tenzir-bench compare \
  --base docker://ghcr.io/tenzir/tenzir:v5.18.0 \
  --candidate docker://ghcr.io/tenzir/tenzir:main \
  examples/benchmarks \
  --compact
```

Without `--compact`, the command prints a detailed comparison and the source
path for each staged report. As with `run`, use `--dry-run --verbose` to inspect
invocations without executing them.

Place Tenzir options after the build that should receive them. Options with
values must use the `--option=value` form:

```sh
tenzir-bench compare \
  --base /path/to/baseline/bin/tenzir \
  --candidate /path/to/candidate/bin/tenzir --console-verbosity=debug \
  examples/benchmarks
```

## Synchronize reference results

Download published reference results:

```sh
tenzir-bench sync
```

By default, the command downloads results for published releases and the most
recent published `main` build that match known GitHub artifact identifiers. Use
`--full` to synchronize all architectures.

GitHub metadata is cached for 30 minutes. Pass `--refresh` to bypass this cache.
Set `GITHUB_TOKEN` to authenticate GitHub API requests and avoid anonymous rate
limits.

## Evaluate results

Compare the latest local runs with synchronized references:

```sh
tenzir-bench eval --runs ~/.local/state/tenzir-bench/results --compact
```

By default, the most recent release is the baseline and the latest published
`main` build is an additional comparison. If no `main` results exist, the
command warns and omits that comparison.

Use `--base` to select an explicit baseline directory. Without `--compact`, the
JSON output includes raw measurements, absolute differences, and percentage
changes for every measured attribute.

Run `tenzir-bench sync` before evaluation to fetch reference results. If no
release or `main` references are available, compare local builds with
`tenzir-bench compare` instead.

## Publish results

Upload reports to an object storage destination:

```sh
tenzir-bench publish \
  --runs ~/.local/state/tenzir-bench/results \
  --destination s3://tenzir-benchmarks/main
```

Publishing is idempotent and skips existing artifacts. Pass `--force` to
replace them. Authentication uses the standard AWS credential configuration.

Published reports should retain metadata that identifies the benchmark
definition and Tenzir build. This lets evaluation reject incompatible
baselines.

## Author benchmarks

A benchmark has its own directory with one `bench.yaml` manifest and one or
more `.tql` implementation files:

```text
examples/benchmarks/suricata_parse_json/
├── bench.yaml
└── default.tql
```

The harness injects a `BENCHMARK_INPUT_<NAME>_PATH` environment variable for
each named input. A benchmark with one input also receives
`BENCHMARK_INPUT_PATH` for compatibility. If the manifest defines
`output.path`, the harness injects `BENCHMARK_OUTPUT_PATH`.

Fixtures can provision external systems, seed selected inputs, and run hooks
before and after each measurement. Runners wrap Tenzir to collect metrics such
as wall-clock time, CPU use, peak resident memory, hardware counters, and cache
statistics. The `time` runner measures a `tenzir` process and is the default.
The `deployment` runner [runs the pipeline in a
deployment](#run-benchmarks-in-a-deployment).

Read the [benchmark authoring guide](../examples/benchmarks/README.md) for the
manifest schema, fixture behavior, and complete examples.
