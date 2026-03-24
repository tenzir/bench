import unittest
from pathlib import Path

from tenzir_bench.compare import _cache_key, _unique_labels
from tenzir_bench.executor import BuildInfo, build_result_id


class CompareHelpersTest(unittest.TestCase):
    def test_cache_key_distinguishes_same_version_binaries(self) -> None:
        info_a = BuildInfo(version="v1.2.3", build_type="Release", path="/tmp/a/bin/tenzir")
        info_b = BuildInfo(version="v1.2.3", build_type="Release", path="/tmp/b/bin/tenzir")

        cache_a = _cache_key(info_a, Path("/tmp/a/bin/tenzir"), ())
        cache_b = _cache_key(info_b, Path("/tmp/b/bin/tenzir"), ())

        self.assertNotEqual(cache_a, cache_b)

    def test_cache_key_distinguishes_same_binary_with_different_options(self) -> None:
        info = BuildInfo(version="v1.2.3", build_type="Release", path="/tmp/a/bin/tenzir")

        cache_a = _cache_key(info, Path("/tmp/a/bin/tenzir"), ())
        cache_b = _cache_key(info, Path("/tmp/a/bin/tenzir"), ("--neo",))

        self.assertNotEqual(cache_a, cache_b)

    def test_duplicate_labels_get_disambiguated(self) -> None:
        labels = _unique_labels(
            ["v1.2.3", "v1.2.3"],
            [Path("/tmp/a/bin/tenzir"), Path("/tmp/b/bin/tenzir")],
        )

        self.assertEqual(len(set(labels)), 2)
        self.assertTrue(all(label.startswith("v1.2.3[") for label in labels))

    def test_build_result_id_distinguishes_forwarded_tenzir_args(self) -> None:
        info = BuildInfo(version="v1.2.3", build_type="Release", path="/tmp/a/bin/tenzir")

        self.assertEqual(build_result_id(info, ()), "v1.2.3")
        self.assertNotEqual(build_result_id(info, ("--neo",)), "v1.2.3")
