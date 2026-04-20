from __future__ import annotations

import pytest
from sellerclaw_agent.async_backoff import (
    ping_interval_after_error,
    ping_interval_success,
    ping_interval_when_suspended,
    sse_interval_after_error,
)

pytestmark = pytest.mark.unit


def test_ping_interval_success_in_expected_range() -> None:
    for _ in range(50):
        s = ping_interval_success()
        assert 8.5 <= s <= 11.5


def test_ping_interval_when_suspended_in_expected_range() -> None:
    for _ in range(50):
        s = ping_interval_when_suspended()
        assert 180.0 <= s <= 210.0


@pytest.mark.parametrize(
    ("errors", "min_expected", "max_expected"),
    [
        pytest.param(1, 10.0, 13.0, id="first-error"),
        pytest.param(2, 20.0, 23.0, id="second-error"),
        pytest.param(5, 160.0, 163.0, id="fifth-error"),
        pytest.param(10, 300.0, 303.0, id="capped-at-300"),
    ],
)
def test_ping_interval_after_error_grows_and_caps(errors: int, min_expected: float, max_expected: float) -> None:
    s = ping_interval_after_error(errors)
    assert min_expected <= s <= max_expected


def test_sse_interval_after_error_doubles_with_cap() -> None:
    assert 4.0 <= sse_interval_after_error(2.0) <= 30.5
    assert sse_interval_after_error(20.0) <= 30.5
