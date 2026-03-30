from pathlib import Path

from tenzir_bench.pr_comment import render_grouped_markdown
from tenzir_bench.reports import Report


def _report(
    benchmark_id: str,
    implementation_id: str,
    *,
    seconds: float,
    rss_kb: int,
) -> Report:
    return Report(
        path=Path(f"/tmp/{benchmark_id}-{implementation_id}.json"),
        pipeline=f"{benchmark_id}/{implementation_id}",
        benchmark_id=benchmark_id,
        implementation_id=implementation_id,
        wall_clock=seconds,
        rss_kb=rss_kb,
        build_version="v1.2.3",
        artifact_id="v1.2.3",
    )


def test_render_grouped_markdown_groups_builds_by_implementation() -> None:
    markdown = render_grouped_markdown(
        [
            (
                "candidate",
                {
                    "from_kafka_1m/neo": _report("from_kafka_1m", "neo", seconds=1.0, rss_kb=1024),
                    "from_kafka_1m/legacy": _report("from_kafka_1m", "legacy", seconds=2.0, rss_kb=2048),
                },
            ),
            (
                "main",
                {
                    "from_kafka_1m/neo": _report("from_kafka_1m", "neo", seconds=1.5, rss_kb=1536),
                    "from_kafka_1m/legacy": _report("from_kafka_1m", "legacy", seconds=2.5, rss_kb=3072),
                },
            ),
        ],
    )

    neo_candidate = markdown.index("| neo | candidate |")
    neo_main = markdown.index("| neo | main |")
    legacy_candidate = markdown.index("| legacy | candidate |")
    assert neo_candidate < neo_main < legacy_candidate
    assert "## from_kafka_1m" in markdown
