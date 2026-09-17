"""The quota alert's whole value is that it stays quiet until it shouldn't."""

from __future__ import annotations

import pytest

from app.workers.quota_watch import ALARM_AT, FREE_TIER_CHARS, WARN_AT, band


def test_silence_is_the_normal_case() -> None:
    """Full coverage of every seed is roughly 4,200 characters against a million. An alert that
    arrives every day is one nobody reads on the day it matters."""
    assert band(0) is None
    assert band(4_244) is None
    assert band(int(FREE_TIER_CHARS * WARN_AT) - 1) is None


@pytest.mark.parametrize(
    ("used", "expected"),
    [
        (int(FREE_TIER_CHARS * WARN_AT), "warn"),
        (int(FREE_TIER_CHARS * ALARM_AT) - 1, "warn"),
        (int(FREE_TIER_CHARS * ALARM_AT), "alarm"),
        (FREE_TIER_CHARS - 1, "alarm"),
        (FREE_TIER_CHARS, "over"),
        (FREE_TIER_CHARS * 3, "over"),
    ],
)
def test_each_threshold_is_reported_once_it_is_reached(used: int, expected: str) -> None:
    assert band(used) == expected


def test_the_free_tier_is_a_parameter_not_a_constant_in_the_maths() -> None:
    """Neural2 gets a million characters a month; Standard and WaveNet get four. Changing voice
    changes the denominator, and the function must follow rather than hard-code one of them."""
    assert band(2_000_000, free_tier=4_000_000) == "warn"
    assert band(2_000_000, free_tier=1_000_000) == "over"
