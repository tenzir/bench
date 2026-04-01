import subprocess
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from tenzir_bench import fixtures as fixture_api
from tenzir_bench.definitions import BenchmarkDefinition, BenchmarkRuntime
from tenzir_bench.kafka_fixture import KafkaFixtureOptions


class KafkaFixtureTest(unittest.TestCase):
    def test_kafka_fixture_starts_compose_and_reseeds_topic(self) -> None:
        commands: list[list[str]] = []
        published_payloads: list[bytes] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            compose_file = root / "benchmarks" / "integrations" / "compose.yaml"
            compose_file.parent.mkdir(parents=True, exist_ok=True)
            compose_file.write_text("services: {}\n", encoding="utf-8")
            benchmark_path = compose_file.parent / "suricata-from-kafka.tql"
            benchmark_path.write_text("discard\n", encoding="utf-8")
            dataset = root / "suricata-eve.json"
            dataset.write_text('{"event_type":"flow"}\n{"event_type":"dns"}\n', encoding="utf-8")
            definition = BenchmarkDefinition(
                path=benchmark_path,
                id="suricata-from-kafka",
                description=None,
                tags={},
                min_version=None,
                max_version=None,
                input_path="suricata/eve.json",
                input_source=None,
                input_events=2,
                input_measure=True,
                output_path=None,
                output_measure=False,
                env={},
                fixtures=(fixture_api.FixtureSpec(name="kafka", options={"topic": "bench"}),),
                tenzir_args=[],
                runner="time",
                runtime=BenchmarkRuntime(),
                pipeline_body="discard",
            )

            def _fake_run(  # noqa: ANN001
                cmd,
                cwd=None,
                stdin=None,
                capture_output=None,
                text=None,
                check=None,
            ):
                del capture_output, text, check
                text_cmd = [str(part) for part in cmd]
                commands.append(text_cmd)
                if text_cmd == ["docker", "compose", "version"]:
                    return subprocess.CompletedProcess(text_cmd, 0, stdout="compose ok", stderr="")
                return subprocess.CompletedProcess(text_cmd, 0, stdout="", stderr="")

            class _FakeStdin(BytesIO):
                def __init__(self) -> None:
                    super().__init__()
                    self.payload = b""

                def close(self) -> None:
                    self.payload = self.getvalue()
                    super().close()

            class _FakePopen:
                def __init__(self, cmd, cwd=None, stdin=None, stdout=None, stderr=None):  # noqa: ANN001
                    del cwd, stdin, stdout, stderr
                    self.cmd = [str(part) for part in cmd]
                    commands.append(self.cmd)
                    self.stdin = _FakeStdin()
                    self.returncode = 0

                def communicate(self):  # noqa: ANN201
                    published_payloads.append(self.stdin.payload)
                    return (b"", b"")

                def kill(self) -> None:
                    return None

                def wait(self) -> int:
                    return self.returncode

            token = fixture_api.push_context(
                fixture_api.FixtureContext(
                    definition=definition,
                    dataset_path=dataset,
                    output_root=root / "out",
                    env={},
                )
            )
            try:
                with (
                    patch(
                        "tenzir_bench.kafka_fixture.shutil.which", return_value="/usr/bin/docker"
                    ),
                    patch("tenzir_bench.kafka_fixture.subprocess.run", side_effect=_fake_run),
                    patch("tenzir_bench.kafka_fixture.subprocess.Popen", side_effect=_FakePopen),
                    patch("tenzir_bench.kafka_fixture.time.sleep"),
                    fixture_api.activate(definition.fixtures) as env,
                ):
                    self.assertEqual(env["BENCHMARK_KAFKA_BOOTSTRAP_SERVERS"], "127.0.0.1:9092")
                    self.assertEqual(env["BENCHMARK_KAFKA_TOPIC"], "bench")
                    self.assertTrue(env["BENCHMARK_KAFKA_GROUP_ID"].startswith("tenzir-bench-"))
                    hook_env = dict(env)
                    fixture_api.invoke_active_hook(
                        "before_run",
                        input_path=dataset,
                        output_path=None,
                        phase="measurement",
                        run_index=0,
                        env=hook_env,
                        command=(),
                    )
                    self.assertTrue(hook_env["BENCHMARK_KAFKA_GROUP_ID"].endswith("-measurement-0"))
            finally:
                fixture_api.pop_context(token)

        self.assertEqual(len(published_payloads), 1)
        self.assertEqual(
            published_payloads[0],
            (b'{"event_type":"flow"}\n{"event_type":"dns"}\n' * 500),
        )
        flat_commands = [" ".join(command) for command in commands]
        self.assertTrue(any("docker compose version" == command for command in flat_commands))
        self.assertTrue(any(" up -d redpanda" in command for command in flat_commands))
        self.assertTrue(
            any(" rpk topic list --brokers 127.0.0.1:9092" in command for command in flat_commands)
        )
        self.assertTrue(
            any(
                " rpk topic delete bench --brokers 127.0.0.1:9092" in command
                for command in flat_commands
            )
        )
        self.assertTrue(
            any(
                " rpk topic create bench --partitions 1 --replicas 1 --brokers 127.0.0.1:9092"
                in command
                for command in flat_commands
            )
        )
        self.assertEqual(
            sum(
                " rpk topic produce bench --brokers 127.0.0.1:9092" in command
                for command in flat_commands
            ),
            1,
        )
        self.assertTrue(
            any(" down --volumes --remove-orphans" in command for command in flat_commands)
        )

    def test_kafka_fixture_reports_missing_docker_compose(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            compose_file = root / "benchmarks" / "integrations" / "compose.yaml"
            compose_file.parent.mkdir(parents=True, exist_ok=True)
            compose_file.write_text("services: {}\n", encoding="utf-8")
            definition = BenchmarkDefinition(
                path=compose_file.parent / "suricata-from-kafka.tql",
                id="suricata-from-kafka",
                description=None,
                tags={},
                min_version=None,
                max_version=None,
                input_path="suricata/eve.json",
                input_source=None,
                input_events=2,
                input_measure=True,
                output_path=None,
                output_measure=False,
                env={},
                fixtures=(
                    fixture_api.FixtureSpec(
                        name="kafka",
                        options=KafkaFixtureOptions(topic="bench").__dict__,
                    ),
                ),
                tenzir_args=[],
                runner="time",
                runtime=BenchmarkRuntime(),
                pipeline_body="discard",
            )
            token = fixture_api.push_context(
                fixture_api.FixtureContext(
                    definition=definition,
                    dataset_path=root / "input.json",
                    output_root=root / "out",
                    env={},
                )
            )
            try:
                with (
                    patch("tenzir_bench.kafka_fixture.shutil.which", return_value=None),
                    self.assertRaises(fixture_api.FixtureUnavailable),
                ):
                    with fixture_api.activate(definition.fixtures):
                        pass
            finally:
                fixture_api.pop_context(token)
