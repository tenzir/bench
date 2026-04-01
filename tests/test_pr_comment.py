from pathlib import Path

from tenzir_bench.pr_comment import BuildDisplay, render_grouped_markdown
from tenzir_bench.reports import Report


def _report(
    benchmark_id: str,
    implementation_id: str,
    *,
    target: str = "static",
    version: str = "v1.2.3",
    seconds: float,
    rss_kb: int,
) -> Report:
    return Report(
        path=Path(f"/tmp/{benchmark_id}-{implementation_id}.json"),
        pipeline=f"{benchmark_id}/{implementation_id}",
        benchmark_id=benchmark_id,
        implementation_id=implementation_id,
        target=target,
        hardware_key="local_x86_64_unknown_8c",
        wall_clock=seconds,
        rss_kb=rss_kb,
        build_version=version,
        artifact_id=version,
    )


def test_render_grouped_markdown_groups_builds_by_implementation() -> None:
    markdown = render_grouped_markdown(
        [
            (
                "candidate",
                {
                    "from_kafka_1m/neo": _report("from_kafka_1m", "neo", seconds=1.0, rss_kb=1024),
                    "from_kafka_1m/legacy": _report(
                        "from_kafka_1m", "legacy", seconds=2.0, rss_kb=2048
                    ),
                },
            ),
            (
                "main",
                {
                    "from_kafka_1m/neo": _report("from_kafka_1m", "neo", seconds=1.5, rss_kb=1536),
                    "from_kafka_1m/legacy": _report(
                        "from_kafka_1m", "legacy", seconds=2.5, rss_kb=3072
                    ),
                },
            ),
        ],
    )

    neo_main = markdown.index("| neo | main |")
    neo_candidate = markdown.index("| neo | candidate |")
    legacy_candidate = markdown.index("| legacy | candidate |")
    assert neo_main < neo_candidate < legacy_candidate
    assert "## from_kafka_1m" in markdown


def test_render_grouped_markdown_orders_release_main_extra_candidate_with_adjacent_targets() -> (
    None
):
    markdown = render_grouped_markdown(
        [
            BuildDisplay(
                label="candidate docker",
                role="candidate",
                target="docker",
                implicit=True,
                reports={
                    "b/neo": _report(
                        "b", "neo", target="docker", version="v5.31.0", seconds=11.0, rss_kb=2200
                    )
                },
            ),
            BuildDisplay(
                label="candidate static",
                role="candidate",
                target="static",
                implicit=True,
                reports={
                    "b/neo": _report(
                        "b", "neo", target="static", version="v5.31.0", seconds=12.0, rss_kb=2400
                    )
                },
            ),
            BuildDisplay(
                label="main docker",
                role="main",
                target="docker",
                implicit=True,
                reports={
                    "b/neo": _report(
                        "b", "neo", target="docker", version="v5.30.1", seconds=10.0, rss_kb=2000
                    )
                },
            ),
            BuildDisplay(
                label="main static",
                role="main",
                target="static",
                implicit=True,
                reports={
                    "b/neo": _report(
                        "b", "neo", target="static", version="v5.30.1", seconds=10.5, rss_kb=2100
                    )
                },
            ),
            BuildDisplay(
                label="docker@feature-a",
                role="extra",
                target="docker",
                ref="feature-a",
                request_index=0,
                reports={
                    "b/neo": _report(
                        "b", "neo", target="docker", version="v5.30.2", seconds=10.8, rss_kb=2050
                    )
                },
            ),
            BuildDisplay(
                label="static@feature-a",
                role="extra",
                target="static",
                ref="feature-a",
                request_index=1,
                reports={
                    "b/neo": _report(
                        "b", "neo", target="static", version="v5.30.2", seconds=11.2, rss_kb=2150
                    )
                },
            ),
            BuildDisplay(
                label="docker@v5.29.0",
                role="release",
                target="docker",
                ref="v5.29.0",
                request_index=2,
                reports={
                    "b/neo": _report(
                        "b", "neo", target="docker", version="v5.29.0", seconds=9.0, rss_kb=1800
                    )
                },
            ),
            BuildDisplay(
                label="static@v5.29.0",
                role="release",
                target="static",
                ref="v5.29.0",
                request_index=3,
                reports={
                    "b/neo": _report(
                        "b", "neo", target="static", version="v5.29.0", seconds=9.4, rss_kb=1850
                    )
                },
            ),
            BuildDisplay(
                label="latest stable docker",
                role="release",
                target="docker",
                ref="v5.30.0",
                implicit=True,
                reports={
                    "b/neo": _report(
                        "b", "neo", target="docker", version="v5.30.0", seconds=9.5, rss_kb=1900
                    )
                },
            ),
            BuildDisplay(
                label="latest stable static",
                role="release",
                target="static",
                ref="v5.30.0",
                implicit=True,
                reports={
                    "b/neo": _report(
                        "b", "neo", target="static", version="v5.30.0", seconds=9.8, rss_kb=1950
                    )
                },
            ),
        ]
    )

    rows = [
        "| neo | docker@v5.29.0 |",
        "| neo | static@v5.29.0 |",
        "| neo | latest stable docker |",
        "| neo | latest stable static |",
        "| neo | main docker |",
        "| neo | main static |",
        "| neo | docker@feature-a |",
        "| neo | static@feature-a |",
        "| neo | candidate docker |",
        "| neo | candidate static |",
    ]
    indices = [markdown.index(row) for row in rows]
    assert indices == sorted(indices)
    assert "| neo | main static | 10.50 | +5.0% | 2 MB | +5.0% |" in markdown
    assert "| neo | candidate static | 12.00 | +20.0% | 2 MB | +20.0% |" in markdown


def test_render_grouped_markdown_uses_static_main_baseline_when_no_docker_main_exists() -> None:
    markdown = render_grouped_markdown(
        [
            BuildDisplay(
                label="main static",
                role="main",
                target="static",
                implicit=True,
                reports={"b/neo": _report("b", "neo", target="static", seconds=10.0, rss_kb=1000)},
            ),
            BuildDisplay(
                label="candidate static",
                role="candidate",
                target="static",
                implicit=True,
                reports={"b/neo": _report("b", "neo", target="static", seconds=12.0, rss_kb=1200)},
            ),
        ]
    )

    assert "| neo | candidate static | 12.00 | +20.0% | 1 MB | +20.0% |" in markdown
