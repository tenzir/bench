# Tenzir Benchmark Harness

This repository contains a portable Python-based benchmarking harness for the
Tenzir data pipeline engine. The goal is to make it easy to collect and compare
runtime metrics across different versions of Tenzir while exercising realistic
pipelines and operators.

## Getting Started

1. **Enter the development shell** (requires the Nix package manager):

   ```bash
   direnv allow   # or: nix-shell
   ```

2. **Download benchmark datasets**:

   ```bash
   ./benchmarks.py prepare --data-dir benchmark/data
   ```

   This command fetches public Suricata and Zeek fixtures and prepares
   derivative files (CSV, key-value) needed by the example benchmarks.

3. **Run benchmarks**:

   ```bash
   TENZIR_BENCHMARK_DATA=benchmark/data \
     python benchmarks.py run
   ```

   The harness will execute all pipelines found under `benchmark/benchmarks/`,
   capturing runtime statistics and throughput data in JSON format.

4. **Evaluate results**:

   ```bash
   python benchmarks.py eval --base baseline/ --runs benchmark/results --compact
   ```

   This generates a concise wall-time and memory comparison against a baseline
   directory.

## Project Layout

- `benchmarks.py` – CLI entrypoint with `prepare`, `run`, and `eval` commands.
- `benchmark/` – TQL pipelines, documentation, datasets, and results.
- `npins/` – Dependency pins managed by [`npins`](https://github.com/andir/npins).
- `shell.nix` / `.envrc` – Nix-based development shell definition providing
  `uv`, `ruff`, and `basedpyright`.

## Development

We use [`uv`](https://docs.astral.sh/uv/) to manage Python dependencies and
lockfiles. The development shell provides Python 3.13, `uv`, and the `ruff`
code-quality tool. Linting and formatting rules will be defined as the project
evolves.

## License

This project is licensed under the [Apache License, Version 2.0](LICENSE).
