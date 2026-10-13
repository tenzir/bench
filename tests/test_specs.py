import tempfile
import unittest
from pathlib import Path

from tenzir_bench.specs import discover_definitions, load_definitions_from_paths


class SpecsTest(unittest.TestCase):
    def test_discover_definitions_loads_directory_based_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            suite = root / "examples" / "benchmarks" / "suricata_parse_json"
            suite.mkdir(parents=True)
            (suite / "bench.yaml").write_text(
                """description: Parse Suricata events
inputs:
  main:
    path: suricata/eve.json
    repetitions: 1
    source:
      num_events: 1
""",
                encoding="utf-8",
            )
            (suite / "default.tql").write_text(
                """---
bench:
  id: default
---
discard
""",
                encoding="utf-8",
            )

            definitions = discover_definitions(
                None,
                version_supplier=lambda: "v5.30.1",
                root=root,
            )

        self.assertEqual(
            [definition.id for definition in definitions], ["suricata_parse_json/default"]
        )

    def test_discover_definitions_loads_defaults_and_filters_by_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bench_root = root / "bench"
            suite = bench_root / "benchmarks" / "from_kafka_1m"
            suite.mkdir(parents=True)
            (bench_root / "defaults.txt").write_text("from_kafka_*\n", encoding="utf-8")
            (suite / "bench.yaml").write_text(
                """name: from_kafka_1m
description: Kafka ingest
inputs:
  main:
    path: suricata/eve.json
    repetitions: 1
    source:
      url: ../../fixtures/eve.json
      num_events: 1
""",
                encoding="utf-8",
            )
            (suite / "legacy.tql").write_text(
                """---
bench:
  id: legacy
  max_version: "5.29.x"
---
discard
""",
                encoding="utf-8",
            )
            (suite / "neo.tql").write_text(
                """---
bench:
  id: neo
  min_version: "5.30.0"
  tenzir_args:
    - --neo
---
discard
""",
                encoding="utf-8",
            )

            definitions = discover_definitions(
                None,
                version_supplier=lambda: "v5.30.1",
                root=root,
            )

        self.assertEqual([definition.id for definition in definitions], ["from_kafka_1m/neo"])
        self.assertEqual(definitions[0].benchmark_id, "from_kafka_1m")
        self.assertEqual(definitions[0].implementation_id, "neo")
        self.assertEqual(definitions[0].tenzir_args, ["--neo"])
        self.assertEqual(definitions[0].input_source, "../../fixtures/eve.json")

    def test_discover_definitions_skips_excluded_benchmarks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bench_root = root / "bench"
            for name in ("from_kafka_1m", "to_iceberg_layouts"):
                suite = bench_root / "benchmarks" / name
                suite.mkdir(parents=True)
                (suite / "bench.yaml").write_text(
                    f"""name: {name}
inputs:
  main:
    path: suricata/eve.json
    repetitions: 1
""",
                    encoding="utf-8",
                )
                (suite / "default.tql").write_text(
                    """---
bench:
  id: default
---
discard
""",
                    encoding="utf-8",
                )
            (bench_root / "excluded.txt").write_text(
                "# Too slow for CI.\n\nto_iceberg_*\n", encoding="utf-8"
            )

            def discovered(pattern: str | None) -> list[str]:
                definitions = discover_definitions(
                    pattern,
                    version_supplier=lambda: "v5.30.1",
                    root=root,
                )
                return [definition.id for definition in definitions]

            self.assertEqual(discovered(None), ["from_kafka_1m/default"])
            (bench_root / "defaults.txt").write_text("*\n", encoding="utf-8")
            self.assertEqual(discovered(None), ["from_kafka_1m/default"])
            self.assertEqual(discovered("to_iceberg_*"), ["to_iceberg_layouts/default"])

    def test_load_definitions_from_paths_keeps_all_compatible_implementations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            suite = root / "bench" / "benchmarks" / "json_parse"
            suite.mkdir(parents=True)
            (suite / "bench.yaml").write_text(
                """name: json_parse
inputs:
  main:
    path: suricata/eve.json
    repetitions: 1
    source:
      num_events: 1
""",
                encoding="utf-8",
            )
            (suite / "base.tql").write_text(
                """---
bench:
  id: base
  min_version: "5.28.0"
---
discard
""",
                encoding="utf-8",
            )
            (suite / "neo.tql").write_text(
                """---
bench:
  id: neo
  min_version: "5.30.0"
---
discard
""",
                encoding="utf-8",
            )

            definitions = load_definitions_from_paths(
                [suite],
                version_supplier=lambda: "v5.30.1",
                root=root,
            )

        self.assertEqual(
            [definition.id for definition in definitions],
            ["json_parse/base", "json_parse/neo"],
        )

    def test_discover_definitions_supports_exact_globs_on_flat_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bench_root = root / "bench"
            for benchmark_id in ("from_kafka_1m", "json_parse"):
                suite = bench_root / "benchmarks" / benchmark_id
                suite.mkdir(parents=True)
                (suite / "bench.yaml").write_text(
                    """name: example
inputs:
  main:
    path: suricata/eve.json
    repetitions: 1
    source:
      num_events: 1
""",
                    encoding="utf-8",
                )
                (suite / "impl.tql").write_text(
                    """---
bench:
  id: impl
---
discard
""",
                    encoding="utf-8",
                )

            definitions = discover_definitions(
                "from_kafka_*",
                version_supplier=lambda: "v5.30.1",
                root=root,
            )

        self.assertEqual([definition.benchmark_id for definition in definitions], ["from_kafka_1m"])
