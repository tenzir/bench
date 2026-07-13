# 🏁 tenzir-bench

`tenzir-bench` measures the performance of realistic
[Tenzir](https://github.com/tenzir/tenzir) pipelines. It runs repeatable
benchmarks against local binaries or container images and records results for
comparison across builds and releases.

## ✨ Highlights

- ⏱️ **Repeatable measurements**: Run warmups and multiple measurement passes
  with consistent datasets and execution settings.
- 🧩 **Reusable fixtures**: Provision services such as Kafka or a Tenzir node
  and seed them with benchmark inputs.
- 🏎️ **Flexible runners**: Measure wall-clock time, CPU use, peak memory, and
  hardware counters with pluggable runners.
- 🔬 **Build comparisons**: Compare local binaries and Docker images directly,
  or evaluate runs against published release results.

## 📦 Installation

`tenzir-bench` requires Python 3.11 or newer. Run the latest compatible release
from PyPI with [`uvx`](https://docs.astral.sh/uv/concepts/tools/):

```sh
uvx tenzir-bench --help
```

`uvx` downloads the release into an isolated environment and caches subsequent
invocations.

## 🚀 Quick start

Prepare the managed reference datasets:

```sh
uvx tenzir-bench prepare
```

Run the examples against a local Tenzir build:

```sh
uvx tenzir-bench run --tenzir ./build/bin/tenzir --benchmark examples/benchmarks
```

Compare two builds:

```sh
uvx tenzir-bench compare \
  --base ./build/baseline/bin/tenzir \
  --candidate ./build/candidate/bin/tenzir \
  examples/benchmarks \
  --compact
```

Build targets can also use the `docker://` prefix, for example,
`docker://ghcr.io/tenzir/tenzir:main`.

## 📚 Documentation

The documentation currently lives in this repository:

- Read the [command guide](docs/README.md) to run, compare, synchronize,
  evaluate, and publish benchmarks.
- Read the [benchmark authoring guide](examples/benchmarks/README.md) to define
  benchmarks, implementations, inputs, and fixtures.
- Explore the [runnable examples](examples/benchmarks/) for complete benchmark
  layouts.

## 📜 License

`tenzir-bench` is available under the Apache License, Version 2.0. See
[`LICENSE`](LICENSE) for details.
