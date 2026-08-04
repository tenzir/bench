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

Implementations may declare `variants:` that re-run the same pipeline with
different Tenzir arguments. Select them with `--variant`, which accepts globs
and is repeatable:

```sh
tenzir-bench run --tenzir /path/to/tenzir --variant p1 --variant p8
```

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

Restrict a comparison to specific variants with `--variant`. This is useful
when a build does not support the arguments of every variant:

```sh
tenzir-bench compare \
  --base /path/to/baseline/bin/tenzir \
  --candidate /path/to/candidate/bin/tenzir \
  --variant p1 \
  bench/benchmarks/parallel_cpu_bound
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
statistics.

Read the [benchmark authoring guide](../examples/benchmarks/README.md) for the
manifest schema, fixture behavior, and complete examples.
