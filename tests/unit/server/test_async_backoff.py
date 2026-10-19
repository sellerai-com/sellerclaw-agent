from __future__ import annotations

import pytest
from sellerclaw_agent.async_backoff import (
    capped_exponential_backoff,
    is_overload_status,
    ping_interval_after_error,
    ping_interval_success,
    ping_interval_when_suspended,
    sse_backoff_ceiling,
    sse_clean_reconnect_sleep,
    sse_reconnect_sleep,
)

pytestmark = pytest.mark.unit


def test_ping_interval_success_in_expected_range() -> None:
    for _ in range(50):
        s = ping_interval_success()
        assert 8.5 <= s <= 11.5


def test_ping_interval_when_suspended_in_expected_range() -> None:
    for _ in range(50):
        s = ping_interval_when_suspended()
        assert 28.0 <= s <= 30.0


@pytest.mark.parametrize(
    ("errors", "min_expected", "max_expected"),
    [
        pytest.param(1, 10.0, 10.5, id="first-error"),
        pytest.param(2, 20.0, 20.5, id="second-error"),
        pytest.param(5, 30.0, 30.0, id="capped-at-30"),
        pytest.param(10, 30.0, 30.0, id="capped-at-30-long-run"),
        # 2**1024 no longer fits a float: a ~9h cloud outage used to crash the ping loop here.
        pytest.param(1025, 30.0, 30.0, id="first-count-past-float-range"),
        pytest.param(100_000, 30.0, 30.0, id="multi-day-outage"),
    ],
)
def test_ping_interval_after_error_grows_and_caps(errors: int, min_expected: float, max_expected: float) -> None:
    s = ping_interval_after_error(errors)
    assert min_expected <= s <= max_expected


@pytest.mark.parametrize(
    ("failures", "expected"),
    [
        pytest.param(0, 60.0, id="no-failures-is-base"),
        pytest.param(1, 60.0, id="first-failure-is-base"),
        pytest.param(3, 240.0, id="doubles-per-failure"),
        pytest.param(5, 900.0, id="capped"),
        pytest.param(1025, 900.0, id="first-count-past-float-range"),
        pytest.param(100_000, 900.0, id="multi-day-outage"),
    ],
)
def test_capped_exponential_backoff(failures: int, expected: float) -> None:
    assert capped_exponential_backoff(60.0, failures, 900.0) == expected


def test_sse_backoff_ceiling_doubles_with_cap() -> None:
    assert sse_backoff_ceiling(2.0) == 4.0
    assert sse_backoff_ceiling(4.0) == 8.0
    # Caps at 15s for ordinary drops — kept small so a user is never offline for long.
    assert sse_backoff_ceiling(8.0) == 15.0
    assert sse_backoff_ceiling(20.0) == 15.0
    # Never below the 2s base.
    assert sse_backoff_ceiling(0.0) == 2.0


def test_sse_backoff_ceiling_overloaded_uses_higher_cap() -> None:
    # When the cloud is shedding load (429/5xx), back off a bit harder: cap rises to 20s
    # (still bounded — we don't strand the user) and stays above the ordinary 15s cap.
    assert sse_backoff_ceiling(20.0, overloaded=True) == 20.0
    assert sse_backoff_ceiling(30.0, overloaded=True) == 20.0
    assert sse_backoff_ceiling(15.0, overloaded=True) == 20.0
    assert sse_backoff_ceiling(15.0) == 15.0  # ordinary cap is lower


def test_sse_reconnect_sleep_is_full_jitter_within_ceiling() -> None:
    for _ in range(200):
        s = sse_reconnect_sleep(8.0)
        assert 0.0 <= s <= 8.0
    # A non-positive ceiling must not raise or return a negative sleep.
    assert sse_reconnect_sleep(0.0) == 0.0


def test_sse_clean_reconnect_sleep_in_expected_range() -> None:
    for _ in range(200):
        assert 0.0 <= sse_clean_reconnect_sleep() <= 5.0


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        pytest.param(429, True, id="too-many-requests"),
        pytest.param(503, True, id="service-unavailable"),
        pytest.param(502, True, id="bad-gateway"),
        pytest.param(500, True, id="server-error"),
        pytest.param(403, False, id="forbidden"),
        pytest.param(200, False, id="ok"),
        pytest.param(None, False, id="none"),
    ],
)
def test_is_overload_status(status: int | None, expected: bool) -> None:
    assert is_overload_status(status) is expected
