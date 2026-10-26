from e2e.http import split_at_marker


def test_split_at_marker_keeps_remainder() -> None:
    found, rest = split_at_marker(b"banner\nE2E-42-run-host-END\n$", b"E2E-42-run-")
    assert found == b"banner\nE2E-42-run-"
    assert rest == b"host-END\n$"
    found, rest = split_at_marker(rest, b"-END")
    assert found == b"host-END"
    assert rest == b"\n$"


def test_split_at_marker_missing() -> None:
    found, rest = split_at_marker(b"partial", b"-END")
    assert found is None
    assert rest == b"partial"
