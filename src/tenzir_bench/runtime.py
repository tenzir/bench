"""Resolved Tenzir runtime abstraction for benchmarks and fixtures."""

from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import IO, Literal, cast

from .paths import BenchPaths

TargetKind = Literal["static", "docker"]


@dataclass(frozen=True)
class TenzirRuntime:
    """Resolved target-agnostic runtime for invoking Tenzir commands."""

    target: TargetKind
    source: str
    tenzir_path: Path
    tenzir_node_path: Path | None = None

    def command_for_tenzir(self, args: Sequence[str]) -> list[str]:
        return [str(self.tenzir_path), *args]

    def command_for_tenzir_node(self, args: Sequence[str]) -> list[str]:
        if self.tenzir_node_path is None:
            raise RuntimeError(f"{self.source}: tenzir-node is not available for this runtime")
        return [str(self.tenzir_node_path), *args]

    def run_tenzir(
        self,
        *,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
        capture_output: bool = False,
        text: bool = True,
        check: bool = False,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            self.command_for_tenzir(args),
            env=_runtime_env(self.target, env),
            capture_output=capture_output,
            text=text,
            check=check,
            cwd=cwd,
        )
        return cast(subprocess.CompletedProcess[str], completed)

    def popen_tenzir_node(
        self,
        *,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
        stdout: IO[str] | int | None = None,
        stderr: IO[str] | int | None = None,
        text: bool = True,
        cwd: Path | None = None,
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            self.command_for_tenzir_node(args),
            env=_runtime_env(self.target, env),
            stdout=stdout,
            stderr=stderr,
            text=text,
            cwd=cwd,
        )


def resolve_runtime(paths: BenchPaths, value: str) -> TenzirRuntime:
    if value.startswith("docker://"):
        image = value[len("docker://") :].strip()
        if not image:
            raise ValueError("docker image reference must not be empty")
        return _docker_runtime(paths, image)
    candidate = _resolve_local_tenzir_path(value)
    return runtime_from_path(candidate)


def runtime_from_path(path: Path) -> TenzirRuntime:
    resolved = path.resolve()
    if resolved.suffix == ".sh" and resolved.parent.name == "docker":
        tenzir_path, tenzir_node_path = _docker_wrapper_paths(resolved)
        return TenzirRuntime(
            target="docker",
            source=str(tenzir_path),
            tenzir_path=tenzir_path,
            tenzir_node_path=tenzir_node_path,
        )
    node = resolved.with_name("tenzir-node")
    node_path = node if node.exists() else None
    return TenzirRuntime(
        target="static",
        source=str(resolved),
        tenzir_path=resolved,
        tenzir_node_path=node_path,
    )


def _resolve_local_tenzir_path(value: str) -> Path:
    path = Path(value)
    candidate = path / "bin" / "tenzir" if path.is_dir() else path
    if not candidate.exists():
        raise FileNotFoundError(f"No tenzir executable at {candidate}")
    return candidate


def _docker_runtime(paths: BenchPaths, image: str) -> TenzirRuntime:
    wrapper_dir = paths.ensure_dir(paths.state_dir / "docker")
    digest = hashlib.sha256(image.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", image)
    tenzir_wrapper = wrapper_dir / f"{safe}-{digest}.sh"
    tenzir_node_wrapper = wrapper_dir / f"{safe}-{digest}-node.sh"
    _ = tenzir_wrapper.write_text(
        _docker_wrapper_script(image, paths, executable="tenzir"), encoding="utf-8"
    )
    _ = tenzir_node_wrapper.write_text(
        _docker_wrapper_script(image, paths, executable="tenzir-node"),
        encoding="utf-8",
    )
    tenzir_wrapper.chmod(0o755)
    tenzir_node_wrapper.chmod(0o755)
    return TenzirRuntime(
        target="docker",
        source=f"docker://{image}",
        tenzir_path=tenzir_wrapper,
        tenzir_node_path=tenzir_node_wrapper,
    )


def _docker_wrapper_paths(path: Path) -> tuple[Path, Path | None]:
    if path.stem.endswith("-node"):
        tenzir_node_path = path
        tenzir_path = path.with_name(f"{path.stem[:-5]}{path.suffix}")
    else:
        tenzir_path = path
        tenzir_node_path = path.with_name(f"{path.stem}-node{path.suffix}")
    if not tenzir_node_path.exists():
        tenzir_node_path = None
    return tenzir_path, tenzir_node_path


def _runtime_env(target: TargetKind, env: Mapping[str, str] | None) -> dict[str, str] | None:
    if env is None:
        return None
    merged = dict(env)
    if target == "docker":
        forward = {
            name.strip()
            for name in merged.get("TENZIR_BENCH_FORWARD_ENV", "").split(",")
            if name.strip()
        }
        forward.update(name for name in merged if name != "TENZIR_BENCH_FORWARD_ENV")
        merged["TENZIR_BENCH_FORWARD_ENV"] = ",".join(sorted(forward))
    return merged


def _docker_wrapper_script(image: str, paths: BenchPaths, *, executable: str = "tenzir") -> str:
    cache_dir = shlex.quote(str(paths.cache_dir))
    state_dir = shlex.quote(str(paths.state_dir))
    image_ref = shlex.quote(image)
    executable_name = shlex.quote(executable)
    if executable == "tenzir":
        run_body = dedent(
            """\
            ENTRYPOINT_JSON="$(docker image inspect --format '{{json .Config.Entrypoint}}' "$IMAGE")"
            CMD_JSON="$(docker image inspect --format '{{json .Config.Cmd}}' "$IMAGE")"
            PYTHON_WRAPPER="$(cat <<'PY'
            import json
            import os
            import subprocess
            import sys

            entrypoint = json.loads(sys.argv[1]) or []
            default_cmd = json.loads(sys.argv[2]) or []
            sep = sys.argv.index("--")
            runtime_args = sys.argv[sep + 1 :]
            argv = entrypoint + (runtime_args if runtime_args else default_cmd)
            if not argv:
                print("docker image is missing an entrypoint/cmd for tenzir-bench", file=sys.stderr)
                raise SystemExit(127)
            proc = subprocess.Popen(argv)
            _pid, status, rusage = os.wait4(proc.pid, 0)
            exit_code = os.waitstatus_to_exitcode(status)
            if exit_code == 0:
                print(f"tenzir-bench-maxrss={rusage.ru_maxrss}", file=sys.stderr)
            raise SystemExit(exit_code)
            PY
            )"
            exec docker run --rm --network=host --user "$(id -u)":"$(id -g)" "${volumes[@]}" "${forward_envs[@]}" "${workdir_args[@]}" --entrypoint python3 "$IMAGE" -c "$PYTHON_WRAPPER" "$ENTRYPOINT_JSON" "$CMD_JSON" -- "$@"
            """
        )
    else:
        run_body = (
            'exec docker run --rm --network=host --user "$(id -u)":"$(id -g)" '
            '"${volumes[@]}" "${forward_envs[@]}" "${workdir_args[@]}" '
            f'--entrypoint {executable_name} "$IMAGE" "$@"\n'
        )
    script = f"""\
#!/usr/bin/env bash
set -euo pipefail

IMAGE={image_ref}
CACHE_DIR={cache_dir}
STATE_DIR={state_dir}
WORK_DIR="${{PWD}}"

if ! command -v docker >/dev/null 2>&1; then
    echo "docker executable not found" >&2
    exit 127
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker pull "$IMAGE" >&2
fi

declare -a volumes=()

_add_volume() {{
    local dir="$1"
    local mode="${{2:-}}"
    if [[ -z "$dir" || ! -d "$dir" ]]; then
        return
    fi
    local spec="${{dir}}:${{dir}}"
    if [[ -n "$mode" ]]; then
        spec="${{spec}}:${{mode}}"
    fi
    volumes+=("-v" "$spec")
}}

_add_volume "$CACHE_DIR" "ro"
_add_volume "$STATE_DIR"
_add_volume "$WORK_DIR"

declare -a workdir_args=()
if [[ -d "$WORK_DIR" ]]; then
    workdir_args=("-w" "$WORK_DIR")
fi

declare -a env_names=()
if [[ -n "${{TENZIR_BENCH_FORWARD_ENV:-}}" ]]; then
    IFS=',' read -ra env_names <<< "${{TENZIR_BENCH_FORWARD_ENV}}"
fi
declare -a forward_envs=()
for name in "${{env_names[@]}}"; do
    name="${{name//[[:space:]]/}}"
    [[ -z "$name" ]] && continue
    if [[ -n "${{!name-}}" ]]; then
        forward_envs+=("-e" "$name")
    fi
done

{run_body.rstrip()}
"""
    return script
