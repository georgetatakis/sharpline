"""The one game simulator every bet reads.

Given each team's expected points, draws 10,000 games of correlated team scores. Every
probability (win, cover, over) comes from the same draws, so a spread, a moneyline and a
total for one game never contradict each other. The player models will read these runs
too, through the same expected points.

Team scores are bivariate normal. The two spreads below are hand-set from NFL history
(margins have a standard deviation near 13 points, totals near 13.5) and are refitted in
the backtest. A continuous draw has no pushes; key numbers (3, 7) wait for v1.
"""

import math
from dataclasses import dataclass

import numpy as np

N_SIMS = 10_000
MARGIN_SD = 13.0
TOTAL_SD = 13.5

# Per-team score spread and the correlation between the two scores that give those.
TEAM_VAR = (MARGIN_SD**2 + TOTAL_SD**2) / 4
TEAM_SD = math.sqrt(TEAM_VAR)
SCORE_CORR = (TOTAL_SD**2 - MARGIN_SD**2) / (TOTAL_SD**2 + MARGIN_SD**2)


@dataclass
class Simulation:
    home: np.ndarray
    away: np.ndarray

    @property
    def margin(self) -> np.ndarray:
        return self.home - self.away

    @property
    def total(self) -> np.ndarray:
        return self.home + self.away

    def home_win_prob(self) -> float:
        return float(np.mean(self.margin > 0))

    def home_cover_prob(self, spread_home: float) -> float:
        """spread_home is sportsbook style: -3.5 means home must win by more than 3.5."""
        return float(np.mean(self.margin + spread_home > 0))

    def over_prob(self, total: float) -> float:
        return float(np.mean(self.total > total))


def simulate(home_points: float, away_points: float, n: int = N_SIMS, seed: int | None = None) -> Simulation:
    rng = np.random.default_rng(seed)
    cov = TEAM_VAR * np.array([[1.0, SCORE_CORR], [SCORE_CORR, 1.0]])
    draws = rng.multivariate_normal([home_points, away_points], cov, size=n)
    # A team can't score below zero.
    draws = np.clip(draws, 0, None)
    return Simulation(home=draws[:, 0], away=draws[:, 1])
