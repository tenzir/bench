# Repository Instructions

Use `jj`, not `git`, for history operations in this repository.

For every code change in this repository, all of the following checks must pass before the change is considered complete:

- `nix shell nixpkgs#ruff -c ruff check src tests examples/fixtures`
- `uv run basedpyright src`
- `treefmt --ci`

Also run the relevant `pytest` coverage for the code you touch.

Do not commit generated artifacts or caches. In particular:

- `__pycache__/`
- `dist/`

Keep runnable examples aligned with the real benchmark layout:

- one benchmark directory per benchmark id
- one `bench.yaml` per benchmark
- one or more implementation `.tql` files
- fixtures under `examples/fixtures/<fixture-name>/`

Prefer backward-compatible migrations unless the change explicitly removes old schema or API support.
