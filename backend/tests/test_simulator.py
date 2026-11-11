import numpy as np
import pytest

from app.simulator import MARGIN_SD, TOTAL_SD, simulate


def test_equal_teams_are_a_coin_flip():
    sim = simulate(22, 22, seed=1)
    assert sim.home_win_prob() == pytest.approx(0.5, abs=0.015)
    assert sim.home_cover_prob(0) == pytest.approx(0.5, abs=0.015)


def test_draws_have_the_expected_means_and_spreads():
    sim = simulate(27, 20, n=50_000, seed=2)
    assert np.mean(sim.margin) == pytest.approx(7, abs=0.2)
    assert np.mean(sim.total) == pytest.approx(47, abs=0.2)
    assert np.std(sim.margin) == pytest.approx(MARGIN_SD, abs=0.2)
    assert np.std(sim.total) == pytest.approx(TOTAL_SD, abs=0.2)


def test_probabilities_follow_the_line():
    sim = simulate(27, 20, seed=3)
    # Home by 7: laying 3 covers more often than laying 10.
    assert sim.home_cover_prob(-3) > sim.home_cover_prob(-7) > sim.home_cover_prob(-10)
    assert sim.home_cover_prob(-7) == pytest.approx(0.5, abs=0.02)
    assert sim.over_prob(40) > sim.over_prob(47) > sim.over_prob(54)


def test_scores_never_go_below_zero_and_a_seed_repeats():
    a, b = simulate(4, 4, seed=4), simulate(4, 4, seed=4)
    assert a.home.min() >= 0 and a.away.min() >= 0
    assert np.array_equal(a.home, b.home)
