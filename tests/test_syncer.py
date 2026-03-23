import unittest

from tenzir_bench.syncer import _artifact_filters, _artifact_id_from_key, _matches_any_artifact


class SyncerHelpersTest(unittest.TestCase):
    def test_artifact_filters_include_release_tags_and_latest_main(self) -> None:
        filters = _artifact_filters(
            [{"tag": "v1.0.0"}, {"tag": "v1.1.0"}],
            [{"sha": "abcdef1234567890"}, {"sha": "deadbeef"}],
        )

        self.assertEqual(filters, {"v1.0.0", "v1.1.0", "abcdef1234567890"})

    def test_matches_any_artifact_uses_build_id_segment(self) -> None:
        key = "prefix/benchhash/inputhash/main-abcdef1/time/report.json"

        self.assertEqual(_artifact_id_from_key(key), "main-abcdef1")
        self.assertTrue(_matches_any_artifact(key, {"abcdef1234567890"}))
        self.assertFalse(_matches_any_artifact(key, {"v1.2.3"}))
