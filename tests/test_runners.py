from tenzir_bench.runners import _parse_docker_maxrss


def test_parse_docker_maxrss_returns_none_without_marker() -> None:
    assert _parse_docker_maxrss("plain stderr output") is None


def test_parse_docker_maxrss_extracts_marker_value() -> None:
    stderr = "warning\nanother line\ntenzir-bench-maxrss=424242\n"
    assert _parse_docker_maxrss(stderr) == 424242
