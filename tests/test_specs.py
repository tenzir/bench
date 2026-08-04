import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tenzir_bench.definitions import BenchmarkDefinition, BenchmarkError, BenchmarkRuntime
from tenzir_bench.hashing import hash_benchmark
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


class VariantSpecTests(unittest.TestCase):
    def _write_suite(self, root: Path) -> Path:
        suite = root / "bench" / "benchmarks" / "parallel_cpu_bound"
        suite.mkdir(parents=True)
        (suite / "bench.yaml").write_text(
            """name: parallel_cpu_bound
inputs:
  main:
    path: suricata/eve.json
    repetitions: 1
    source:
      num_events: 1
variants:
  p1:
    tenzir_args: ["--parallelism", "1"]
  p8:
    description: Parallelism 8
    tenzir_args: ["--parallelism", "8"]
    tags:
      degree: "8"
""",
            encoding="utf-8",
        )
        (suite / "neo.tql").write_text(
            """---
bench:
  id: neo
  tenzir_args: ["--neo"]
---
discard
""",
            encoding="utf-8",
        )
        return suite

    def test_manifest_variants_expand_into_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            suite = self._write_suite(root)
            definitions = load_definitions_from_paths(
                [suite],
                version_supplier=lambda: "v6.9.0",
                root=root,
            )
        self.assertEqual(
            [definition.id for definition in definitions],
            ["parallel_cpu_bound/neo/p1", "parallel_cpu_bound/neo/p8"],
        )
        self.assertEqual(
            [definition.tenzir_args for definition in definitions],
            [["--neo", "--parallelism", "1"], ["--neo", "--parallelism", "8"]],
        )
        self.assertEqual(definitions[1].description, "Parallelism 8")
        self.assertEqual(definitions[1].tags["degree"], "8")
        self.assertEqual(
            [definition.variant_id for definition in definitions],
            ["p1", "p8"],
        )

    def test_variant_filter_keeps_implementations_without_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            suite = self._write_suite(root)
            plain = root / "bench" / "benchmarks" / "plain"
            plain.mkdir(parents=True)
            (plain / "bench.yaml").write_text(
                """name: plain
inputs:
  main:
    path: a.json
    source:
      num_events: 1
""",
                encoding="utf-8",
            )
            (plain / "neo.tql").write_text(
                """---
bench:
  id: neo
---
discard
""",
                encoding="utf-8",
            )
            definitions = load_definitions_from_paths(
                [suite, plain],
                version_supplier=lambda: "v6.9.0",
                root=root,
                variants=["p1"],
            )
        self.assertEqual(
            [definition.id for definition in definitions],
            ["parallel_cpu_bound/neo/p1", "plain/neo"],
        )

    def test_variant_filter_selects_single_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            suite = self._write_suite(root)
            definitions = load_definitions_from_paths(
                [suite],
                version_supplier=lambda: "v6.9.0",
                root=root,
                variants=["p1"],
            )
        self.assertEqual(
            [definition.id for definition in definitions],
            ["parallel_cpu_bound/neo/p1"],
        )

    def test_implementation_variants_override_manifest_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            suite = root / "bench" / "benchmarks" / "x"
            suite.mkdir(parents=True)
            (suite / "bench.yaml").write_text(
                """name: x
variants:
  shared:
    tenzir_args: ["--shared"]
inputs:
  main:
    path: a.json
    source:
      num_events: 1
""",
                encoding="utf-8",
            )
            (suite / "neo.tql").write_text(
                """---
bench:
  id: neo
  variants:
    own:
      tenzir_args: ["--own"]
---
discard
""",
                encoding="utf-8",
            )
            definitions = load_definitions_from_paths(
                [suite],
                version_supplier=lambda: "v6.9.0",
                root=root,
            )
        self.assertEqual([definition.id for definition in definitions], ["x/neo/own"])
        self.assertEqual(definitions[0].tenzir_args, ["--own"])

    def test_variant_options_reject_non_mapping_values(self) -> None:
        for malformed in ("[]", "false", '""'):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                suite = root / "bench" / "benchmarks" / "x"
                suite.mkdir(parents=True)
                (suite / "bench.yaml").write_text(
                    f"""name: x
variants:
  p1: {malformed}
inputs:
  main:
    path: a.json
    source:
      num_events: 1
""",
                    encoding="utf-8",
                )
                (suite / "neo.tql").write_text(
                    """---
bench:
  id: neo
---
discard
""",
                    encoding="utf-8",
                )
                with self.assertRaises(BenchmarkError):
                    _ = load_definitions_from_paths(
                        [suite],
                        version_supplier=lambda: "v6.9.0",
                        root=root,
                    )

    def test_variant_options_accept_null_shorthand(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            suite = root / "bench" / "benchmarks" / "x"
            suite.mkdir(parents=True)
            (suite / "bench.yaml").write_text(
                """name: x
variants:
  p1:
inputs:
  main:
    path: a.json
    source:
      num_events: 1
""",
                encoding="utf-8",
            )
            (suite / "neo.tql").write_text(
                """---
bench:
  id: neo
---
discard
""",
                encoding="utf-8",
            )
            definitions = load_definitions_from_paths(
                [suite],
                version_supplier=lambda: "v6.9.0",
                root=root,
            )
        self.assertEqual([definition.id for definition in definitions], ["x/neo/p1"])

    def test_variant_identity_changes_the_benchmark_hash(self) -> None:
        base = BenchmarkDefinition(
            path=Path("examples/benchmarks/x/neo.tql"),
            id="x/neo",
            description=None,
            tags={},
            min_version=None,
            max_version=None,
            inputs={},
            output_path=None,
            env={},
            fixtures=(),
            tenzir_args=["--neo"],
            runner="time",
            runtime=BenchmarkRuntime(warmup_runs=0, measurement_runs=1, timeout_seconds=10),
            pipeline_body="discard",
            benchmark_id="x",
            implementation_id="neo",
        )
        p1 = replace(base, id="x/neo/p1", implementation_id="neo/p1", variant_id="p1")
        p2 = replace(base, id="x/neo/p2", implementation_id="neo/p2", variant_id="p2")
        # A variant with identical execution settings must not reuse the
        # unvarianted result directory, nor collide with a sibling variant.
        self.assertNotEqual(hash_benchmark(base), hash_benchmark(p1))
        self.assertNotEqual(hash_benchmark(p1), hash_benchmark(p2))

    def test_benchmark_hash_is_stable_without_variants(self) -> None:
        definition = BenchmarkDefinition(
            path=Path("examples/benchmarks/x/neo.tql"),
            id="x/neo",
            description=None,
            tags={},
            min_version=None,
            max_version=None,
            inputs={},
            output_path=None,
            env={},
            fixtures=(),
            tenzir_args=["--neo"],
            runner="time",
            runtime=BenchmarkRuntime(warmup_runs=0, measurement_runs=1, timeout_seconds=10),
            pipeline_body="discard",
            benchmark_id="x",
            implementation_id="neo",
        )
        self.assertEqual(
            hash_benchmark(definition),
            "dc5130b0ec1a26fee228159fc0a4afc2dc4a870aadb9b0bafb7e6c5c1693c4ac",
        )
