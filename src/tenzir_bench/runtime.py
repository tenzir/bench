"""Resolved Tenzir runtime abstraction for benchmarks and fixtures."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from types import MappingProxyType
from typing import IO, Final, Literal, cast

from .paths import BenchPaths

TargetKind = Literal["static", "docker"]

# From Tenzir v7 on, `tenzir <pipeline>` no longer runs a pipeline: the first
# argument names a command, unless `TENZIR_UNIFIED` selects the previous command
# interface. Benchmarks and fixtures pass pipelines and options the previous
# way, which every version understands. Earlier versions ignore the variable.
COMMAND_INTERFACE_ENV: Final[Mapping[str, str]] = MappingProxyType({"TENZIR_UNIFIED": "0"})

# The name under which `tenzir` runs the deployment instead of a command.
DEPLOYMENT_EXECUTABLE: Final = "tenzir-up"

# Starts the line on which a Docker deployment reports its resource usage: the
# user and system CPU seconds and the peak resident set size in kilobytes.
DEPLOYMENT_RUSAGE_MARKER: Final = "tenzir-bench-rusage="


@dataclass(frozen=True)
class TenzirRuntime:
    """Resolved target-agnostic runtime for invoking Tenzir commands."""

    target: TargetKind
    source: str
    tenzir_path: Path
    tenzir_node_path: Path | None = None
    tenzir_up_path: Path | None = None

    def command_for_tenzir(self, args: Sequence[str]) -> list[str]:
        return [str(self.tenzir_path), *args]

    def command_for_tenzir_node(self, args: Sequence[str]) -> list[str]:
        if self.tenzir_node_path is None:
            raise RuntimeError(f"{self.source}: tenzir-node is not available for this runtime")
        return [str(self.tenzir_node_path), *args]

    def command_for_deployment(self, args: Sequence[str]) -> list[str]:
        """Return the command that starts a deployment, as `tenzir-up` does."""
        up_path = self.tenzir_up_path
        if up_path is None and self.target == "static":
            up_path = _deployment_alias(self.tenzir_path)
        if up_path is None:
            raise RuntimeError(f"{self.source}: deployments are not available for this runtime")
        return [str(up_path), *args]

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

    def popen_deployment(
        self,
        *,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
        stdout: IO[str] | int | None = None,
        stderr: IO[str] | int | None = None,
        text: bool = True,
        cwd: Path | None = None,
    ) -> subprocess.Popen[str]:
        """Start a deployment.

        For a Docker target, the process reports the resource usage of the
        deployment on its standard error when it exits, as a line that
        `DEPLOYMENT_RUSAGE_MARKER` starts.
        """
        return subprocess.Popen(
            self.command_for_deployment(args),
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
        tenzir_path, tenzir_node_path, tenzir_up_path = _docker_wrapper_paths(resolved)
        return TenzirRuntime(
            target="docker",
            source=str(tenzir_path),
            tenzir_path=tenzir_path,
            tenzir_node_path=tenzir_node_path,
            tenzir_up_path=tenzir_up_path,
        )
    node = resolved.with_name("tenzir-node")
    node_path = node if node.exists() else None
    return TenzirRuntime(
        target="static",
        source=str(resolved),
        tenzir_path=resolved,
        tenzir_node_path=node_path,
    )


def _deployment_alias(tenzir_path: Path) -> Path:
    """Return a `tenzir-up` that runs the deployment of a local build.

    A build installs no such executable: its `tenzir` runs the deployment when
    started under that name.
    """
    digest = hashlib.sha256(str(tenzir_path).encode("utf-8")).hexdigest()[:8]
    alias = BenchPaths.create().state_dir / "static" / digest / DEPLOYMENT_EXECUTABLE
    if alias.is_symlink() and alias.readlink() == tenzir_path:
        return alias
    alias.parent.mkdir(parents=True, exist_ok=True)
    # Replace atomically, as a concurrent run may start the alias meanwhile.
    staged = alias.with_name(f"{alias.name}.{os.getpid()}")
    staged.unlink(missing_ok=True)
    staged.symlink_to(tenzir_path)
    _ = staged.replace(alias)
    return alias


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
    tenzir_up_wrapper = wrapper_dir / f"{safe}-{digest}-up.sh"
    for wrapper, executable in (
        (tenzir_wrapper, "tenzir"),
        (tenzir_node_wrapper, "tenzir-node"),
        (tenzir_up_wrapper, DEPLOYMENT_EXECUTABLE),
    ):
        _ = wrapper.write_text(
            _docker_wrapper_script(image, paths, executable=executable), encoding="utf-8"
        )
        wrapper.chmod(0o755)
    return TenzirRuntime(
        target="docker",
        source=f"docker://{image}",
        tenzir_path=tenzir_wrapper,
        tenzir_node_path=tenzir_node_wrapper,
        tenzir_up_path=tenzir_up_wrapper,
    )


def _docker_wrapper_paths(path: Path) -> tuple[Path, Path | None, Path | None]:
    stem = path.stem
    for suffix in ("-node", "-up"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    tenzir_path = path.with_name(f"{stem}{path.suffix}")
    tenzir_node_path = path.with_name(f"{stem}-node{path.suffix}")
    tenzir_up_path = path.with_name(f"{stem}-up{path.suffix}")
    return (
        tenzir_path,
        tenzir_node_path if tenzir_node_path.exists() else None,
        tenzir_up_path if tenzir_up_path.exists() else None,
    )


def _runtime_env(target: TargetKind, env: Mapping[str, str] | None) -> dict[str, str] | None:
    # The wrapper script selects the command interface for Docker targets.
    if target == "static":
        return {**(os.environ if env is None else env), **COMMAND_INTERFACE_ENV}
    if env is None:
        return None
    merged = dict(env)
    if target == "docker":
        forward = {
            name.strip()
            for name in merged.get("TENZIR_BENCH_FORWARD_ENV", "").split(",")
            if name.strip()
        }
        forward.update(
            name
            for name, value in merged.items()
            if name != "TENZIR_BENCH_FORWARD_ENV" and os.environ.get(name) != value
        )
        merged["TENZIR_BENCH_FORWARD_ENV"] = ",".join(sorted(forward))
    return merged


def _docker_wrapper_script(image: str, paths: BenchPaths, *, executable: str = "tenzir") -> str:
    cache_dir = shlex.quote(str(paths.cache_dir))
    state_dir = shlex.quote(str(paths.state_dir))
    logs_dir = shlex.quote(str(paths.state_dir / "logs"))
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
    elif executable == DEPLOYMENT_EXECUTABLE:
        # An image installs no `tenzir-up`: its `tenzir` runs the deployment
        # when started under that name. The container's resource usage is not
        # visible from outside, so the wrapper reports it.
        run_body = dedent(
            f"""\
            PYTHON_WRAPPER="$(cat <<'PY'
            import os
            import shutil
            import signal
            import subprocess
            import sys
            import tempfile

            tenzir = shutil.which("tenzir")
            if tenzir is None:
                print("docker image is missing a tenzir executable", file=sys.stderr)
                raise SystemExit(127)
            alias = os.path.join(tempfile.mkdtemp(), "{DEPLOYMENT_EXECUTABLE}")
            os.symlink(tenzir, alias)
            proc = subprocess.Popen([alias, *sys.argv[1:]])
            for signum in (signal.SIGINT, signal.SIGTERM):
                signal.signal(signum, lambda received, _frame: proc.send_signal(received))
            _pid, status, rusage = os.wait4(proc.pid, 0)
            print(
                f"{DEPLOYMENT_RUSAGE_MARKER}"
                f"{{rusage.ru_utime}} {{rusage.ru_stime}} {{rusage.ru_maxrss}}",
                file=sys.stderr,
            )
            exit_code = os.waitstatus_to_exitcode(status)
            raise SystemExit(exit_code if exit_code >= 0 else 128 - exit_code)
            PY
            )"
            exec docker run --rm --network=host --user "$(id -u)":"$(id -g)" "${{volumes[@]}}" "${{forward_envs[@]}}" "${{workdir_args[@]}}" --entrypoint python3 "$IMAGE" -c "$PYTHON_WRAPPER" "$@"
            """
        )
    else:
        run_body = (
            'exec docker run --rm --network=host --user "$(id -u)":"$(id -g)" '
            '"${volumes[@]}" "${forward_envs[@]}" "${workdir_args[@]}" '
            f'--entrypoint {executable_name} "$IMAGE" "$@"\n'
        )
    command_interface_envs = "\n".join(
        f"forward_envs+=({shlex.quote('-e')} {shlex.quote(f'{name}={value}')})"
        for name, value in COMMAND_INTERFACE_ENV.items()
    )
    script = f"""\
#!/usr/bin/env bash
set -euo pipefail

IMAGE={image_ref}
CACHE_DIR={cache_dir}
STATE_DIR={state_dir}
LOGS_DIR={logs_dir}
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
mkdir -p "$LOGS_DIR"
_add_volume "$LOGS_DIR"
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
forward_envs+=("-e" "CACHE_DIRECTORY=$CACHE_DIR")
forward_envs+=("-e" "STATE_DIRECTORY=$STATE_DIR")
forward_envs+=("-e" "LOGS_DIRECTORY=$LOGS_DIR")
for name in "${{env_names[@]}}"; do
    name="${{name//[[:space:]]/}}"
    [[ -z "$name" ]] && continue
    if [[ -n "${{!name-}}" ]]; then
        forward_envs+=("-e" "$name")
    fi
done
{command_interface_envs}

{run_body.rstrip()}
"""
    return script
