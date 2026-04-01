import unittest
from pathlib import PurePosixPath

from tenzir_bench.syncer import _reference_prefixes


class SyncerHelpersTest(unittest.TestCase):
    def test_reference_prefixes_include_release_tags_and_latest_main(self) -> None:
        prefixes = _reference_prefixes(
            [{"tag": "v1.0.0"}, {"tag": "v1.1.0"}],
            [{"sha": "abcdef1234567890"}, {"sha": "deadbeef"}],
        )

        self.assertEqual(
            prefixes,
            {
                PurePosixPath("refs/tags/v1.0.0"),
                PurePosixPath("refs/tags/v1.1.0"),
                PurePosixPath("refs/main/abcdef1234567890"),
            },
        )
