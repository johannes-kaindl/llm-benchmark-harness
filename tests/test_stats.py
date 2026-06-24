import math

from touchstone import stats


def test_percentile_interpolates_like_numpy():
    data = [1.0, 2.0, 3.0, 4.0]
    assert stats.percentile(data, 50) == 2.5
    assert stats.percentile(data, 0) == 1.0
    assert stats.percentile(data, 100) == 4.0


def test_percentile_p95_on_known_set():
    data = list(range(1, 101))  # 1..100
    # type-7 percentile: rank = 0.95*99 = 94.05 → between index 94 (95) and 95 (96)
    assert math.isclose(stats.percentile([float(x) for x in data], 95), 95.05)


def test_percentile_empty_is_nan():
    assert math.isnan(stats.percentile([], 50))


def test_percentile_single_value():
    assert stats.percentile([7.0], 95) == 7.0


def test_median():
    assert stats.median([3.0, 1.0, 2.0]) == 2.0
    assert math.isnan(stats.median([]))


def test_cv_percent():
    # mean 10, sample stdev of [8,10,12] = 2 → cv = 20%
    assert math.isclose(stats.cv_percent([8.0, 10.0, 12.0]), 20.0)


def test_cv_percent_needs_two_samples():
    assert math.isnan(stats.cv_percent([5.0]))


def test_cv_percent_zero_mean_is_nan():
    assert math.isnan(stats.cv_percent([-1.0, 1.0]))
