from datetime import datetime

from openrag.modules.usage.service import period_bounds


def test_period_bounds_clamps_reset_day_and_crosses_year() -> None:
    start, reset = period_bounds(datetime(2026, 2, 28, 12), 31)
    assert start == datetime(2026, 2, 28)
    assert reset == datetime(2026, 3, 31)

    start, reset = period_bounds(datetime(2026, 1, 1), 15)
    assert start == datetime(2025, 12, 15)
    assert reset == datetime(2026, 1, 15)
