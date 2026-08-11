"""Kiểm thử công thức RP hiện hành.

Chạy nhanh:
    python test_rp_engine.py

Chạy bằng pytest:
    pytest -q test_rp_engine.py
"""

import random

from modules.rp_engine import calculate_deltas, validate_deltas
from modules.rp_formula import RP_FORMULA_VERSION

EXPECTED_RP_FORMULA_VERSION = "RP_V1.14.5"


def rank_level(points):
    return 1 if int(points or 0) < 1100 else 2


def calculate(player_a, player_b, score_a, score_b, seed=1):
    return calculate_deltas(
        player_a,
        player_b,
        score_a,
        score_b,
        rank_level,
        rng=random.Random(seed),
    )


def test_placement_win_and_loss_ranges():
    newcomer = {"rank_points": 1000, "total_matches": 0, "streak": 0, "loss_streak": 0}
    placement = [calculate(newcomer, newcomer, 1, 0, seed) for seed in range(1000)]

    wins = [win for win, _ in placement]
    losses = [-loss for _, loss in placement]
    assert min(wins) >= 22
    assert max(wins) <= 29
    assert min(losses) >= 14
    assert max(losses) <= 19


def test_regular_loss_distribution():
    regular = {"rank_points": 1000, "total_matches": 11, "streak": 0, "loss_streak": 0}
    results = [calculate(regular, regular, 1, 0, seed) for seed in range(2000)]
    deductions = [-loss for _, loss in results]

    assert min(deductions) == 19
    assert max(deductions) == 23
    assert set(deductions) == {19, 20, 21, 22, 23}


def test_loss_streak_ranges():
    regular = {"rank_points": 1000, "total_matches": 11, "streak": 0, "loss_streak": 0}
    expected_ranges = [
        (3, 22, 24),
        (4, 23, 26),
        (5, 25, 27),
        (6, 25, 30),
        (10, 25, 30),
    ]

    for current, minimum, maximum in expected_ranges:
        loser = dict(regular, loss_streak=current)
        results = [calculate(regular, loser, 1, 0, seed) for seed in range(2000)]
        deductions = [-loss for _, loss in results]
        assert min(deductions) == minimum
        assert max(deductions) == maximum


def test_loss_recovery_win_steps():
    regular = {"rank_points": 1000, "total_matches": 11, "streak": 0, "loss_streak": 0}
    recovering_first = dict(regular, loss_streak=5, loss_recovery_win_step=1)
    recovering_second = dict(regular, loss_streak=0, loss_recovery_win_step=2)
    recovering_third = dict(regular, loss_streak=0, loss_recovery_win_step=0)

    assert calculate(recovering_first, regular, 1, 0, 1)[0] == 17
    assert calculate(recovering_second, regular, 1, 0, 1)[0] == 19
    assert calculate(recovering_third, regular, 1, 0, 1)[0] >= 20


def test_draw_points_by_rp_gap():
    assert calculate({"rank_points": 900}, {"rank_points": 1200}, 0, 0) == (3, 3)
    assert calculate({"rank_points": 900}, {"rank_points": 1400}, 0, 0) == (6, 0)
    assert calculate({"rank_points": 1400}, {"rank_points": 900}, 0, 0) == (0, 6)


def test_invalid_zero_loss_delta_is_rejected():
    try:
        validate_deltas(1, 0, 22, 0)
    except ValueError:
        return
    raise AssertionError("Delta thua bằng 0 phải bị từ chối")


def test_formula_version_is_current():
    assert RP_FORMULA_VERSION == EXPECTED_RP_FORMULA_VERSION


def run():
    tests = [
        test_placement_win_and_loss_ranges,
        test_regular_loss_distribution,
        test_loss_streak_ranges,
        test_loss_recovery_win_steps,
        test_draw_points_by_rp_gap,
        test_invalid_zero_loss_delta_is_rejected,
        test_formula_version_is_current,
    ]
    for test in tests:
        test()
    print(f"OK - RP Engine {EXPECTED_RP_FORMULA_VERSION} ({len(tests)} tests)")


if __name__ == "__main__":
    run()
