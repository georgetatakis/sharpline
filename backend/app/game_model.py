"""v0 game model: spread, moneyline and total from one simulator, with hand-set weights.

For each game in a week:
1. Team ratings: each stat below, read from the source the overlap test chose for it,
   using only rows from before the game's week (app.features). Early in a season the
   current numbers are thin, so they are blended with last season's, pulled halfway to
   the league average; the current season's weight grows with games played.
2. Expected points per team: points per drive from the matchup (offense against the
   opposing defense, 40% from points per drive and 60% from EPA per play) times the
   expected drives. Then home field, rest, the manual QB-out
   flag and wind.
3. 10,000 simulated games (app.simulator) give the win probability, the cover
   probability against the posted spread and the over probability against the posted
   total.

Rows are written to model_predictions and locked: a prediction is never rewritten, and a
game that has kicked off is never predicted. The weights are starting values written down
here; the backtest (step 19) measures them and v1 learns its own.

Stats left out of v0: pressure, box counts and coverage have no nflverse history yet
(participation data for 2026 isn't published), so the backtest couldn't measure them.
They join once the overlap test passes them.

Run with:  python -m app.game_model predict --season 2026 --week 10
           python -m app.game_model qb-out --season 2026 --week 10 --team KC [--clear]
"""

import argparse
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features import feature_source, feature_sources, team_stats_before
from app.models import Game, GameLine, ModelPrediction, Team, TeamDefenseStats, TeamOffenseStats
from app.run_log import logged_run
from app.simulator import N_SIMS, simulate

MODEL_VERSION = "game-v0"

OFFENSE_STATS = ["points_per_drive", "epa_per_play", "rz_td_rate", "drives_per_game", "plays_per_drive"]
DEFENSE_STATS = ["points_per_drive_allowed", "epa_per_play_allowed", "rz_td_rate_allowed"]

# Hand-set weights. Each line says where the number comes from.
# Points per drive from the matchup: 40% from points per drive itself, 60% from EPA per
# play turned into points per drive (EPA per play x plays per drive). EPA is the steadier
# stat week to week; the backtest also tries 50/50.
W_PPD = 0.4
W_EPA = 0.6
# Red zone TD rate is off in v0: it swings a lot from year to year, and points per drive
# already counts most of it. (About 0.3 red zone trips per drive x 4 more points for a TD
# than a field goal would make 1.2 points per drive per unit of rate.)
RZ_POINTS_PER_DRIVE = 0.0
# Home field is worth about 1.5 points of margin in recent seasons; none at a neutral site.
HOME_FIELD = 1.5
# Each extra day of rest over the opponent is worth 0.25 points of margin, capped at 4
# days (a bye against a normal week is about 1 point).
REST_POINTS_PER_DAY = 0.25
REST_CAP_DAYS = 4
# A starting QB out moves a spread 3 to 7 points; v0 takes 4.5 off that team.
QB_OUT_POINTS = 4.5
# Wind of 15 mph or more outdoors takes about 2 points off the total.
WIND_MPH = 15
WIND_TOTAL_POINTS = 2.0
# Early-season blending: the current season counts gp / (gp + PRIOR_GAMES), and last
# season is pulled PRIOR_REGRESSION of the way back to the league average.
PRIOR_GAMES = 6
PRIOR_REGRESSION = 0.5
# Floor on a team's expected points, so a lopsided matchup can't go negative.
MIN_POINTS = 3.0


@dataclass
class Ratings:
    """Every team's blended stats, the league averages, and the latest week read."""

    teams: dict[int, dict[str, float]]
    league: dict[str, float]
    as_of_week: int


@dataclass
class GameContext:
    neutral_site: bool = False
    home_rest_days: int | None = None
    away_rest_days: int | None = None
    home_qb_out: bool = False
    away_qb_out: bool = False
    roof: str | None = None
    wind: float | None = None


@dataclass
class GamePrediction:
    home_points: float
    away_points: float
    home_win_prob: float
    home_cover_prob: float | None
    over_prob: float | None
    breakdown: dict = field(default_factory=dict)

    @property
    def margin(self) -> float:
        return self.home_points - self.away_points

    @property
    def total(self) -> float:
        return self.home_points + self.away_points


def _side_rows(session, model, season, target_week, stats, sources) -> tuple[dict, int]:
    """{team_id: {stat: value, "games": n}} for one side, each stat from its own source."""
    by_source: dict[str, list[str]] = {}
    side = "offense" if model is TeamOffenseStats else "defense"
    for stat in stats:
        by_source.setdefault(feature_source(side, stat, sources), []).append(stat)
    out: dict[int, dict] = {}
    latest = 0
    for source, source_stats in by_source.items():
        for row in team_stats_before(session, model, season, target_week, source):
            entry = out.setdefault(row.team_id, {})
            # Screenshot rows don't carry games played; a week's row covers about that many.
            entry["games"] = max(entry.get("games", 0), row.games_played or row.as_of_week)
            latest = max(latest, row.as_of_week)
            for stat in source_stats:
                if getattr(row, stat) is not None:
                    entry[stat] = getattr(row, stat)
    return out, latest


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def team_ratings(session: Session, season: int, week: int, sources: dict | None = None) -> Ratings:
    """Blend this season (before `week`) with last season's final rows, stat by stat."""
    sources = sources if sources is not None else feature_sources()
    team_ids = sorted(session.scalars(select(Team.id)))
    teams: dict[int, dict[str, float]] = {t: {} for t in team_ids}
    league: dict[str, float] = {}
    as_of_week = 0
    for model, stats in ((TeamOffenseStats, OFFENSE_STATS), (TeamDefenseStats, DEFENSE_STATS)):
        current, latest = _side_rows(session, model, season, week, stats, sources)
        # Last season's latest rows: every week of it is before this game.
        prior, _ = _side_rows(session, model, season - 1, 10_000, stats, sources)
        as_of_week = max(as_of_week, latest)
        for stat in stats:
            cur = {t: current.get(t, {}).get(stat) for t in team_ids}
            old = {t: prior.get(t, {}).get(stat) for t in team_ids}
            cur_mean, prior_mean = _mean(cur.values()), _mean(old.values())
            if cur_mean is None and prior_mean is None:
                raise RuntimeError(f"no {stat} before {season} week {week} or in {season - 1}; "
                                   "run app.nflverse_build first")
            center = cur_mean if cur_mean is not None else prior_mean
            for t in team_ids:
                regressed = center
                if old[t] is not None:
                    regressed += (1 - PRIOR_REGRESSION) * (old[t] - prior_mean)
                games = current[t]["games"] if cur[t] is not None else 0
                w = games / (games + PRIOR_GAMES)
                teams[t][stat] = w * cur[t] + (1 - w) * regressed if games else regressed
            league[stat] = _mean(teams[t][stat] for t in team_ids)
    return Ratings(teams=teams, league=league, as_of_week=as_of_week)


def expected_points(offense: dict, defense: dict, opponent_offense: dict, league: dict) -> tuple[float, dict]:
    """One team's expected points against a defense, before game context."""
    ppd_matchup = offense["points_per_drive"] + defense["points_per_drive_allowed"] - league["points_per_drive_allowed"]
    epa_gap = ((offense["epa_per_play"] - league["epa_per_play"])
               + (defense["epa_per_play_allowed"] - league["epa_per_play_allowed"]))
    ppd_from_epa = league["points_per_drive"] + epa_gap * league["plays_per_drive"]
    rz_gap = ((offense["rz_td_rate"] - league["rz_td_rate"])
              + (defense["rz_td_rate_allowed"] - league["rz_td_rate_allowed"]))
    ppd = W_PPD * ppd_matchup + W_EPA * ppd_from_epa + RZ_POINTS_PER_DRIVE * rz_gap
    # Possessions alternate, so both teams get about the same number of drives.
    drives = (offense["drives_per_game"] + opponent_offense["drives_per_game"]) / 2
    return ppd * drives, {"ppd": round(ppd, 3), "drives": round(drives, 2)}


def predict_game(home: dict, away: dict, league: dict, context: GameContext,
                 spread_home: float | None = None, total: float | None = None,
                 n: int = N_SIMS, seed: int | None = None) -> GamePrediction:
    """Expected points for both teams, game context, then the simulator's probabilities."""
    home_pts, home_parts = expected_points(home, away, away, league)
    away_pts, away_parts = expected_points(away, home, home, league)
    adjust = {"home_field": 0.0, "rest": 0.0, "home_qb_out": 0.0, "away_qb_out": 0.0, "wind": 0.0}
    if not context.neutral_site:
        adjust["home_field"] = HOME_FIELD
    if context.home_rest_days is not None and context.away_rest_days is not None:
        gap = max(-REST_CAP_DAYS, min(REST_CAP_DAYS, context.home_rest_days - context.away_rest_days))
        adjust["rest"] = gap * REST_POINTS_PER_DAY
    # Margin adjustments are split half to each team so the total doesn't move.
    home_pts += (adjust["home_field"] + adjust["rest"]) / 2
    away_pts -= (adjust["home_field"] + adjust["rest"]) / 2
    if context.home_qb_out:
        adjust["home_qb_out"] = -QB_OUT_POINTS
        home_pts -= QB_OUT_POINTS
    if context.away_qb_out:
        adjust["away_qb_out"] = -QB_OUT_POINTS
        away_pts -= QB_OUT_POINTS
    if context.wind is not None and context.wind >= WIND_MPH and context.roof in (None, "outdoors", "open"):
        adjust["wind"] = -WIND_TOTAL_POINTS
        home_pts -= WIND_TOTAL_POINTS / 2
        away_pts -= WIND_TOTAL_POINTS / 2
    home_pts, away_pts = max(home_pts, MIN_POINTS), max(away_pts, MIN_POINTS)

    sim = simulate(home_pts, away_pts, n=n, seed=seed)
    return GamePrediction(
        home_points=home_pts,
        away_points=away_pts,
        home_win_prob=sim.home_win_prob(),
        home_cover_prob=sim.home_cover_prob(spread_home) if spread_home is not None else None,
        over_prob=sim.over_prob(total) if total is not None else None,
        breakdown={"home": home_parts, "away": away_parts, "adjust": adjust},
    )


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite hands back naive datetimes; they were stored as UTC.
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def rest_days(session: Session, game: Game, team_id: int) -> int | None:
    """Days since the team's previous game this season; None in week 1 or without kickoffs."""
    if game.kickoff_time is None:
        return None
    previous = session.scalar(
        select(Game.kickoff_time)
        .where(Game.season == game.season, Game.week < game.week, Game.kickoff_time.is_not(None),
               (Game.home_team_id == team_id) | (Game.away_team_id == team_id))
        .order_by(Game.kickoff_time.desc()).limit(1))
    if previous is None:
        return None
    return (_aware(game.kickoff_time).date() - _aware(previous).date()).days


def current_line(session: Session, game: Game, now: datetime) -> GameLine | None:
    """The latest current line captured before now; closing lines come after kickoff."""
    lines = session.scalars(
        select(GameLine).where(GameLine.game_id == game.id, GameLine.line_type == "current")
        .order_by(GameLine.captured_at.desc())).all()
    return next((line for line in lines if _aware(line.captured_at) <= now), None)


def predict_week(session: Session, season: int, week: int, now: datetime | None = None,
                 n: int = N_SIMS, sources: dict | None = None, log_dir: Path | None = None) -> dict:
    """Predict and lock every game in the week that hasn't kicked off or been predicted."""
    now = now or datetime.now(UTC)
    with logged_run("game_model", log_dir, season=season, week=week, model_version=MODEL_VERSION) as log:
        games = session.scalars(
            select(Game).where(Game.season == season, Game.week == week).order_by(Game.kickoff_time, Game.id)).all()
        if not games:
            raise RuntimeError(f"no {season} week {week} games loaded; load the schedule first")
        ratings = team_ratings(session, season, week, sources)
        counts = {"predicted": 0, "already_locked": 0, "started": 0}
        for game in games:
            if game.status == "final" or (game.kickoff_time is not None and _aware(game.kickoff_time) <= now):
                counts["started"] += 1
                continue
            locked = session.scalar(select(ModelPrediction.id).where(
                ModelPrediction.game_id == game.id, ModelPrediction.model_type == "game",
                ModelPrediction.model_version == MODEL_VERSION))
            if locked is not None:
                counts["already_locked"] += 1
                continue
            line = current_line(session, game, now)
            spread_home = line.spread_home if line else None
            total = line.total if line else None
            context = GameContext(
                neutral_site=game.neutral_site,
                home_rest_days=rest_days(session, game, game.home_team_id),
                away_rest_days=rest_days(session, game, game.away_team_id),
                home_qb_out=game.home_qb_out, away_qb_out=game.away_qb_out,
                roof=game.roof, wind=game.wind,
            )
            # A fixed seed per game, so a rerun of the same inputs gives the same numbers.
            seed = zlib.crc32(f"{MODEL_VERSION}:{game.id}".encode())
            pred = predict_game(ratings.teams[game.home_team_id], ratings.teams[game.away_team_id],
                                ratings.league, context, spread_home, total, n=n, seed=seed)
            common = dict(game_id=game.id, model_type="game", model_version=MODEL_VERSION,
                          snapshot_as_of_week=ratings.as_of_week, locked_at=now)
            session.add_all([
                ModelPrediction(**common, bet_type="spread", predicted_value=-pred.margin,
                                predicted_prob=pred.home_cover_prob, market_line=spread_home),
                ModelPrediction(**common, bet_type="moneyline", predicted_value=pred.margin,
                                predicted_prob=pred.home_win_prob),
                ModelPrediction(**common, bet_type="total", predicted_value=pred.total,
                                predicted_prob=pred.over_prob, market_line=total),
            ])
            counts["predicted"] += 1
            log.event("game", game_id=game.id, nflverse_game_id=game.nflverse_game_id,
                      home_points=round(pred.home_points, 2), away_points=round(pred.away_points, 2),
                      home_win_prob=round(pred.home_win_prob, 4), spread_home=spread_home, total=total,
                      home_cover_prob=pred.home_cover_prob, over_prob=pred.over_prob, **pred.breakdown)
        session.commit()
        log.summary.update(counts=counts, snapshot_as_of_week=ratings.as_of_week)
    return {"season": season, "week": week, "model_version": MODEL_VERSION,
            "snapshot_as_of_week": ratings.as_of_week, **counts}


def set_qb_out(session: Session, season: int, week: int, team_id: int, out: bool = True) -> Game:
    """Flag (or clear) a team's starting QB as out for its game that week."""
    game = session.scalar(select(Game).where(
        Game.season == season, Game.week == week,
        (Game.home_team_id == team_id) | (Game.away_team_id == team_id)))
    if game is None:
        raise LookupError(f"team {team_id} has no {season} week {week} game")
    locked = session.scalar(select(ModelPrediction.id).where(ModelPrediction.game_id == game.id))
    if locked is not None:
        raise RuntimeError("this game is already predicted and locked; the flag would not change it")
    if game.home_team_id == team_id:
        game.home_qb_out = out
    else:
        game.away_qb_out = out
    session.commit()
    return game


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    predict = commands.add_parser("predict", help="predict and lock a week's games")
    qb = commands.add_parser("qb-out", help="flag a team's starting QB as out")
    for p in (predict, qb):
        p.add_argument("--season", type=int, required=True)
        p.add_argument("--week", type=int, required=True)
    qb.add_argument("--team", required=True, help="abbreviation or name, e.g. KC")
    qb.add_argument("--clear", action="store_true", help="remove the flag")
    args = parser.parse_args()

    from app.db import SessionLocal
    from app.team_lookup import TeamLookup

    with SessionLocal() as session:
        if args.command == "predict":
            print(predict_week(session, args.season, args.week))
            return
        team_id = TeamLookup(session).team_id(args.team)
        if team_id is None:
            raise SystemExit(f"unknown team {args.team!r}")
        game = set_qb_out(session, args.season, args.week, team_id, out=not args.clear)
        print(f"game {game.id}: home_qb_out={game.home_qb_out}, away_qb_out={game.away_qb_out}")


if __name__ == "__main__":
    main()
