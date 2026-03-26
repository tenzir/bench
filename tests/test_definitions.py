import tempfile
import unittest
from pathlib import Path

from tenzir_bench.definitions import BenchmarkError, parse_benchmark_file


class DefinitionsTest(unittest.TestCase):
    def test_parse_benchmark_file_supports_fixture_specs(self) -> None:
        source = """---
benchmark:
  id: example
  input:
    path: suricata/eve.json
    events: 1
    measure: true
  fixtures:
    - kafka:
        topic: bench
        partitions: 1
    - sink
---
discard
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "benchmark.tql"
            path.write_text(source, encoding="utf-8")
            definition = parse_benchmark_file(path)

        self.assertEqual(definition.fixtures[0].name, "kafka")
        self.assertEqual(
            definition.fixtures[0].options,
            {"topic": "bench", "partitions": 1},
        )
        self.assertEqual(definition.fixtures[1].name, "sink")

    def test_parse_benchmark_file_supports_singular_fixture_alias(self) -> None:
        source = """---
benchmark:
  id: example
  input:
    path: suricata/eve.json
    events: 1
    measure: true
  fixture: sink
---
discard
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "benchmark.tql"
            path.write_text(source, encoding="utf-8")
            definition = parse_benchmark_file(path)

        self.assertEqual(len(definition.fixtures), 1)
        self.assertEqual(definition.fixtures[0].name, "sink")

    def test_parse_benchmark_file_rejects_conflicting_fixture_keys(self) -> None:
        source = """---
benchmark:
  id: example
  input:
    path: suricata/eve.json
    events: 1
    measure: true
  fixture: sink
  fixtures: [kafka]
---
discard
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "benchmark.tql"
            path.write_text(source, encoding="utf-8")
            with self.assertRaises(BenchmarkError):
                parse_benchmark_file(path)
