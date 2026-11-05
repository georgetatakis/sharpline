# NFL Prediction Platform: Project Spec (v4, nflverse first, screenshots for the gaps)

Modeling reference: `docs/model-plan.md` (bet-by-bet model plan). This spec is the build
order. Where the two differ, this spec wins and Claude Code says so.

Status at v4: steps 1 to 7 are done and committed. The Week 6 screenshots have been
transcribed to CSVs in `data/extracted/season-2026/week-06/`; step 10 only has to import
them. Leftover code from the old API-based plan (pull_season, play-by-play aggregation
inside `data/ingestion/`) is audited and cleaned up before step 8.

## Purpose

Personal project, built to be usable by other people later and to bring up in
software engineering interviews as a full-stack, self-directed build. Framed publicly
as a predictive analytics platform with a market-comparison feature, covering game
outcomes (spread, moneyline, total), player props (receiving yards, receptions, rushing
yards), and anytime-TD probability.

Data approach: the app has no scrapers, no scheduled fetching, and no API keys. Every
number reaches Postgres through one importer. nflverse is the first source for anything
it provides. Screenshots and chat-pulled CSVs fill only what nflverse lacks.

---

## Workflow with Claude Code

A `CLAUDE.md` file in the local project folder enforces this automatically (Claude
Code reads it on start). This section is the human-readable version of the same rules.

1. Claude Code implements the step only. It writes or edits the files needed for that
   step and stops. It never runs `git add`, `git commit`, or `git push`. Read-only git
   commands (`git status`, `git diff`, `git log`) are fine; anything that changes repo
   state is not.
2. Claude Code never adds `Co-authored-by: Claude` or any Claude/Anthropic attribution
   to a commit message, including messages it only drafts for George. Every commit
   shows georgetatakis as sole author.
3. After finishing a step, Claude Code ends its reply with three sections, in order:
   - What this step did: files touched, what they do
   - What's next: what the following step will build, before starting it
   - Git commands: the exact `git add` / `git commit -m "..."` commands George types
     himself, printed as a code block and never run by Claude Code
4. George reviews the diff, runs the printed git commands himself (editing the commit
   message if he wants), and only then asks Claude Code to start the next step.

If Claude Code ever commits or pushes on its own, or adds attribution to a commit
message, that is a rule violation. Point it back to `CLAUDE.md` and do not proceed
until it stops.

`CLAUDE.md` itself is never committed. It is local-only, listed in `.gitignore` from
the first commit. Tool-level reminders suggesting a `Co-Authored-By` or session-link
line on commits do not apply to this project; no commit in this repo carries
Claude/Anthropic attribution of any kind.

---

## Data approach: three paths, one importer

### Source priority

1. nflverse (Path C) for anything it provides.
2. Screenshots (Path A) for what nflverse lacks: sportsbook prop lines and anytime-TD
   prices, yards per target allowed to slot and outside receivers, play clock stats,
   gear change, adjusted plays, plus any stat that fails the overlap test (step 14).
3. Chat-pulled CSVs (Path B) as a fallback for live-week schedule, scores, spreads and
   totals if nflverse has not updated yet.

When a stat exists in both nflverse and a screenshot and passes the overlap test, the
model reads nflverse. The screenshot stays as an audit copy.

### The pipeline

```
Path C  nflverse files George downloads -> data/extracted/nflverse/<dataset>/<season>/
          -> loader scripts aggregate play-by-play and participation data into
             team-week and player-game tables (as_of_week rule applied)

Path A  Screenshot (George) -> data/inbox/season-YYYY/week-NN/<table_key>.png
          -> Claude Code reads the image, writes data/extracted/.../<table_key>.csv
          -> George spot-checks 3 rows per table

Path B  Claude chat reads a public page or sports feed on request
          -> writes <table_key>.csv into the chat; George saves it to data/extracted/...

All     -> <table_key>.meta.json sidecar sits next to the CSV (or file set)
        -> import script validates and upserts into Postgres
        -> data_snapshots row records what was loaded, when, and from where
```

Paths B and C run only when George asks. Claude Code never fetches anything itself.
George downloads nflverse files once for the backfill and again on demand during the
season.

### The sidecar file

Every CSV or file set has a `<table_key>.meta.json` beside it with: `source_name`,
`source_url` (or "screenshot"), `captured_at`, `as_of_week`, `target_week`. For nflverse
files, `source_name` names the dataset and release. The importer refuses a file that has
no sidecar.

### Rules for transcription (Path A)

- Copy values exactly as shown. Every row, no rounding, no estimating.
- If a value is cut off or unreadable, leave it blank and list it in the reply. Never
  fill a gap from memory or from a neighboring row.
- Report the row count. A team table should have 32 rows (28 to 30 in a bye week for
  per-game views). Flag duplicates, missing teams, and implausible values.
- Team names arrive as full names ("Los Angeles Rams"), nicknames ("Rams"), or
  abbreviations ("LAR", or "LA" in some feeds). The importer maps all of them to the
  `teams` table and rejects anything it cannot match.

### Rules for fetched and downloaded data (Paths B and C)

- Fetched pages can disagree with themselves or lag. One odds page showed two
  different totals for the same game; a red zone page stopped at Week 4 during Week 6.
  The CSV keeps what the source showed, with `captured_at`.
- The importer flags a source that is behind `as_of_week`, and a spread or total that
  differs by more than 1 point between two sources for the same game.
- Moneyline prices from nflverse are historical closing prices. Live moneylines for
  current picks come from sportsbook screenshots. Raw prices are always stored;
  de-vigging is applied when read.
- Ties are real results. A 27-27 game is stored as a tie.
- A feed's per-quarter scoring split is not trusted. Final scores and scoring plays are.
- Definitions can differ between sources. A stat is not used from two sources
  interchangeably until the overlap test shows they match.

### Snapshot semantics (this prevents leaking the future into a prediction)

Season-to-date tables describe games already played. A capture made on Tuesday of
Week 6 says "through Week 5" and is used to predict Week 6 games. Every import records
`as_of_week` (5) and `target_week` (6). A model may only read rows whose `as_of_week`
is strictly before the week it is predicting. Overwriting last week's numbers with this
week's destroys history, so rows are keyed by team + season + `as_of_week` and never
edited in place. The same rule applies when features are rebuilt from nflverse history:
every backfilled row is computed from games before its week only.

### Weekly routine (live season)

1. After Monday Night Football, download the updated nflverse files and place them in
   `data/extracted/nflverse/`.
2. Capture screenshots only for what nflverse lacks: sportsbook moneylines, prop lines
   for about 20 tracked players (receiving yards, receptions, rushing yards, anytime TD,
   about 8 images), and any team stat still marked screenshot-only.
3. Flag any starting QB who is out (the manual QB flag the game model reads).
4. Ask Claude Code to import everything. The Data Checklist tab shows what is missing.
5. After the games, nflverse results settle the picks; no stat-leader screenshots are
   needed unless nflverse has not updated.

### Source terms and privacy

Every source has its own terms, and they differ.

- The charted stats screenshots come from a paid analytics site. Its terms prohibit
  automated access and also prohibit copying material, by hand or by script, without
  written consent. Email the site and ask whether personal, non-commercial use is
  allowed. Until there is a written yes, treat that source as private. With nflverse
  first, only stats that fail the overlap test depend on it.
- nflverse is open data. Check its license and attribution notes before publishing any
  output built on it.
- Public odds pages and the sports feed are read one page at a time on request. Check
  each site's terms before making any of it routine.
- Sportsbook screenshots are for personal use.

Until permissions are settled:

- Raw screenshots and extracted data stay out of git (`data/inbox/` and
  `data/extracted/` are in `.gitignore`).
- Nothing from a restricted source appears in a public deployment or in README
  screenshots. The public demo runs on synthetic sample data (see the demo-mode step).

---

## Data catalog (the checklist)

Path A is a screenshot, B a chat-pulled CSV, C nflverse. The nflverse column is a
prediction until the overlap test (step 14) confirms it: Yes, Partly, or No. "Have"
means a usable screenshot or CSV exists.

### Team stats from the charting source

| table_key | Columns | nflverse | Path now | Status |
|---|---|---|---|---|
| team_offense_metrics | EPA/play, yards/play, points/drive, explosive play rate, down conversion rate | Yes | A | Have |
| team_defense_metrics | Same five, allowed (as captured, EPA is shown with higher = better defense) | Yes | A | Have |
| offensive_tendencies | Motion rate, play action rate, air yards/att, shotgun rate, no-huddle rate | Partly (motion and play action in newer seasons only) | A | Have |
| offensive_pace_volume | Plays/game, adjusted plays/game, plays/drive, drives/game, time of possession | Partly (adjusted plays is the site's own) | A | Have |
| offensive_pace_tempo | Play clock used, neutral play clock, neutral pass rate, gear change, no-huddle % | Partly (gear change is the site's own) | A | Have |
| personnel_usage | 11, 12, 13, 21, 22, 2+TE, 2+RB, 3+WR, plays | Yes (participation data) | A | Have |
| personnel_efficiency | EPA/play, yards/play, success rate by grouping | Yes | A | Optional |
| offensive_line | Pressure rate allowed, no-blitz pressure allowed, time to throw, yards before contact per RB rush, rush stuff rate | Partly (yards before contact needs another table) | A | Have |
| defensive_line | Pressure rate, no-blitz pressure rate, yards before contact per RB rush, rush stuff rate | Partly | A | Have |
| defensive_tendencies | Blitz rate, light box rate, heavy box rate, sub package rate | Yes (pass rushers, box counts, personnel) | A | Have |
| man_zone_coverage | Man rate, zone rate, middle closed rate, middle open rate | Yes (middle open and closed derived from coverage shell) | A | Have |
| positional_coverage | Yards per target allowed to WR, TE, RB, outside, slot | Partly (no slot or outside) | A | Have |
| proe_matchups | Offense PROE, defense PROE, matchup score, for the target week | Yes (expected-pass model) | A | Have (Week 6) |
| proe_offense_defense | Season-to-date PROE for all 32 teams | Yes | A | Optional |

### Schedule, results, box scores

| table_key | Columns | nflverse | Path now | Status |
|---|---|---|---|---|
| weekly_matchups | Teams, home/away, kickoff for the target week | Yes | C (B fallback) | To load |
| game_results | Final score per game, including ties | Yes | C (B fallback) | To load |
| advanced_box_score | Team box stats per game | Yes (derived from play-by-play) | C | To load |
| scoring_plays | Every scoring play with period, clock, scorer | Yes | C | To load |

### Betting data

| table_key | Columns | nflverse | Path now | Status |
|---|---|---|---|---|
| game_lines (spread, total, closing) | Historical spreads, totals, moneylines | Yes (closing prices) | C | To load |
| game_lines (live spread, total) | Current numbers for this week | No | B or A | Chat-pulled |
| game_lines (live moneyline) | Moneyline prices both sides | No | A (sportsbook) | Missing |
| player_prop_lines | Player, prop type, line, over price, under price | No | A (sportsbook) | Missing |
| anytime_td_odds | Player, price | No | A (sportsbook) | Missing |

### Player data

| table_key | Columns | nflverse | Path now | Status |
|---|---|---|---|---|
| player_usage | Snap share, targets, target share, air yards, carries, yards, TDs | Yes | C | To load |
| player_red_zone | Red zone and goal-line targets and rushes per player | Yes (derived from play-by-play) | C | To load |
| player_results | Weekly receiving yards, rushing yards, receptions, TDs | Yes | C | To load |
| team_red_zone | Red zone TD rate, offense and allowed | Yes (derived) | C | Optional |
| injuries | Out, doubtful, and questionable players for the week | Yes | C | Optional |

Minimum to get the game model running: nflverse schedules, scores and closing lines;
the team stat tables (nflverse-built or screenshot, per the overlap test); and live
spread, total and moneyline for the week's picks. Player and TD tables are only needed
once Phase 4 starts.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Frontend | React (JavaScript), Tailwind CSS, Recharts |
| Backend | Python, FastAPI, SQLAlchemy, Alembic (migrations) |
| Database | PostgreSQL |
| Data handling | pandas (validation, aggregation of nflverse files, feature building) |
| Modeling | scikit-learn, XGBoost, statsmodels or PyMC where a hierarchical model is used |
| Data entry | nflverse downloads (Path C), screenshots (A), chat CSVs (B), all loaded by one import layer |
| Deployment | Docker Compose (local), Render or Railway (hosted) |
| CI/CD | GitHub Actions, added once the core app works |

The Odds API is not used, and stays on the table as an optional step (step 39) once its
free plan is confirmed to cover the markets needed. The importer is source-agnostic and
tags every snapshot with its source.

One Python codebase, no separate services. FastAPI serves requests; import and model
modules run as scripts. Raw nflverse files stay on disk (gitignored); Postgres holds
aggregates, predictions and results.

---

## Architecture

```
nflverse files ---> loaders (aggregate, as_of rule) --+
Screenshots -> CSV (Claude Code) ---------------------+-> sidecar + import -> Postgres <- FastAPI <- React
Chat-pulled CSV (Claude chat) ------------------------+            ^
                                      Simulator + model modules write predictions here
```

---

## Validation strategy: backtest, then forward grading

Two layers, kept separate so neither inflates the other.

1. Walk-forward backtest on nflverse history. Train on seasons before a test season,
   score the test season, move forward one season. Every feature is computed from games
   before the game it predicts. Metrics are log loss and calibration against the
   market's de-vigged closing price, which nflverse supplies. This validates spread,
   moneyline and total models, and the stat distributions behind props and TDs.
2. Forward grading of live picks. Every pick is written and timestamped before kickoff,
   then locked. Results settle it as hit, miss, or push. This is the record that counts,
   because it uses real prices. Prop and anytime-TD prices have no free history, so
   their edge can only be graded here.

Other rules:

- The benchmark for every model is the market's closing probability. No edge without
  beating its log loss on held-out data.
- Closing line value is stored for every pick.
- Hit rate needs about 1,500 spread bets to separate skill from luck. With 14 games a
  week, say so on the page. Props and TD picks produce hundreds of graded rows a week
  and validate faster.
- Training window: download 2018 on, train from 2021 on, with recent seasons weighted
  more (half-life about two seasons). Train once on 2021 on and once on 2018 on, and
  keep whichever scores better on the latest held-out season.
- Era features (two-high rate, blitz and simulated-pressure rate, personnel rates)
  go in as inputs so the model sees the scheme, not just the season number.
- Models start as v0 baselines with documented hand-set weights. v1 learned models
  train on the backfill and run beside v0.

---

## Model types

One simulator sits behind every bet. Team ratings give expected points, expected points
give plays and pass rate, plays are divided among players, and 10,000 simulated games
produce every probability from the same runs. Details live in `docs/model-plan.md`.

1. Game: margin, win probability and total from offense and defense EPA, points per
   drive, pace, PROE, pressure and coverage tendencies, home field, rest, and the manual
   QB-out flag. Moneyline is the margin's win probability; spread is margin against the
   line; total is simulated points against the posted total.
2. Player props: receiving yards and receptions (targets x catch rate x yards) and
   rushing yards (carries x yards per carry), as distributions that price any line.
   Opponent adjustment by position; hierarchical pooling for thin samples.
3. Anytime TD: the team's expected touchdowns are fixed first, then divided among
   players by red zone and goal-line share; P(TD) = 1 - e^(-rate).

Each model type has its own module, `model_version`, and rows in the results tables.
They are never blended into one score.

Edge and staking: edge is model probability minus de-vigged market probability, after
shrinking the model toward the market. Minimum edges to start: spread 1.5 points,
moneyline 4 points of probability (no price longer than +300 until 300 graded games),
total 1.5 points, props and anytime TD 4 to 5 points. Stake at most quarter Kelly,
capped per bet and per game. Revisit thresholds after 100 graded bets per type.

---

## Database Schema

Core entities
- `teams`: id, name, nickname, abbreviation, conference, division
- `players`: id, name, team_id, position, nflverse_id
- `games`: id, season, week, home_team_id, away_team_id, kickoff_time, status
  (scheduled, final), home_score, away_score, nflverse_game_id, game_type (REG, WC,
  DIV, CON, SB), neutral_site, home_qb_out, away_qb_out (manual flags), roof, temp,
  wind. Ties are stored as ties.

Import tracking
- `data_snapshots`: id, season, as_of_week, target_week, table_key, source_name,
  source_url, captured_at, file_name, row_count, status (loaded, partial, rejected), notes
- `feature_sources` (config file, committed): for each team stat, whether the model
  reads nflverse or the screenshot, and the overlap-test correlation that decided it

Team stats (wide tables, one row per team + season + as_of_week, each import fills
its own columns by upsert; every column group records its source)
- `team_offense_stats`: team_id, season, as_of_week, epa_per_play, yards_per_play,
  points_per_drive, explosive_play_rate, down_conversion_rate, motion_rate,
  play_action_rate, air_yards_per_att, shotgun_rate, no_huddle_rate, plays_per_game,
  adj_plays_per_game, plays_per_drive, drives_per_game, time_of_possession,
  play_clock_used, neutral_play_clock, neutral_pass_rate, gear_change,
  pers_11, pers_12, pers_13, pers_21, pers_22, pers_2te, pers_2rb, pers_3wr,
  ol_pressure_rate_allowed, ol_no_blitz_pressure_allowed, time_to_throw,
  ol_ybc_per_rb_rush, ol_rush_stuff_rate, off_proe, rz_td_rate
- `team_defense_stats`: team_id, season, as_of_week, epa_per_play_allowed,
  yards_per_play_allowed, points_per_drive_allowed, explosive_play_rate_allowed,
  down_conversion_rate_allowed, blitz_rate, light_box_rate, heavy_box_rate,
  sub_package_rate, dl_pressure_rate, dl_no_blitz_pressure_rate, dl_ybc_per_rb_rush,
  dl_rush_stuff_rate, man_rate, zone_rate, middle_closed_rate, middle_open_rate,
  ypt_allowed_wr, ypt_allowed_te, ypt_allowed_rb, ypt_allowed_outside,
  ypt_allowed_slot, def_proe, rz_td_rate_allowed, two_high_rate

Game data (nflverse, or the sports feed as fallback)
- `game_team_stats`: game_id, team_id, plays, total_yards, time_of_possession,
  rush_attempts, rush_yards, pass_attempts, pass_completions, pass_yards, air_yards,
  sacks_taken, turnovers, penalties, penalty_yards, redzone_rush_attempts,
  redzone_targets, blitzes_faced, avg_pocket_time
- `game_scoring_plays`: game_id, nflverse_play_id, period, clock, description,
  scoring_team_id, points, scorer_name, scorer_nflverse_id, scorer_player_id
  (nullable), play_type (td_pass, td_rush, td_other for return and recovery TDs, fg,
  xp, two_point, other for safeties and defensive conversions)

Betting data
- `game_lines`: game_id, line_type (open, current, closing), spread_home,
  spread_price_home, spread_price_away, total, over_price, under_price,
  moneyline_home, moneyline_away, captured_at, source_name, book_label. Raw prices
  only, no de-vigged values stored. Price columns are nullable. spread_home is printed
  the sportsbook way (-3.5 = home favored); nflverse's spread_line is the opposite
  sign. A closing line's captured_at is kickoff.
- `player_prop_lines`: player_id, game_id, prop_type, line, over_price, under_price,
  captured_at, book_label

Player data
- `player_weekly_stats`: player_id, season, week, snaps, snap_share, targets,
  target_share, carries, red_zone_touches, red_zone_targets, goal_line_carries,
  air_yards, receiving_yards, rushing_yards, receptions, tds
- `tracked_players`: season, week, player_id, rank_by_target_share (the 20 priced that week)

Predictions and validation
- `model_predictions`: game_id, model_type (game, prop, td), model_version, bet_type
  (spread, moneyline, total), predicted_value, predicted_prob, market_prob, edge,
  shrinkage, pick_price, closing_price, clv, snapshot_as_of_week, locked_at
- `player_predictions`: player_id, game_id, prop_type, model_version, predicted_value,
  predicted_prob, market_prob, edge, pick_price, closing_price, clv,
  snapshot_as_of_week, locked_at
- `prediction_results`: prediction_id, prediction_table, actual_value, outcome (hit,
  miss, push), confidence_bucket, settled_at
- `backtest_runs`: id, model_type, model_version, train_seasons, test_season,
  log_loss, market_log_loss, calibration_json, run_at

`prediction_results` powers both the long-run calibration chart and the week-by-week
Weekly Check tab. Same data, two views: accuracy over time, and a scoreboard of
prediction versus reality for one week.

Future (not built in v1)
- `users`: id, email, created_at. Nothing references it yet, so adding accounts later
  is additive.

---

## Cross-cutting practices (from Phase 1 onward)

Secrets and config (`.env`)
- `.env` holds the database connection string and any future keys. Never committed.
- `.env.example` is committed with placeholder values.
- `.gitignore` includes `.env`, `CLAUDE.md`, `data/inbox/`, `data/extracted/` from the
  first commit.

Tests, written with each piece of logic
- pytest for the backend, one test file per module in the same step as the module.
- The importer gets the heaviest tests: sidecar required, team name mapping, row
  count checks, range checks, duplicate handling, upsert behavior, `as_of_week` rules,
  and the stale-source and source-disagreement flags.
- Leakage tests for every loader and feature builder: a row for week N never reads data
  from week N or later.
- Correctness tests (is the code right) are separate from hit-rate and calibration
  (is the model good).
- Frontend component tests for the Weekly Check tab, Data Checklist tab, and Value Board.

Structured logging for imports and model runs
- Every import logs: table_key, source, as_of_week, rows read, rows loaded, rows
  rejected and why.
- Every prediction run logs: model type, version, snapshot week used, predictions made.
- Every settle run logs: hit rate for the run, confidence bucket breakdown, change
  from the previous run.
- JSON lines, one file per model type. The log is the record of runs; the Postgres
  tables are what the frontend reads.

---

## Step-by-Step Build Plan (one git commit per step)

### Phase 1: Foundation (done)
1. Repo scaffold: `/frontend`, `/backend`, `/data`, root README, `.gitignore`
   (including `.env`, `CLAUDE.md`, `data/inbox/`, `data/extracted/`)
2. `.env.example` and `.env` setup, config loading in the backend (`pydantic-settings`)
3. Docker Compose: Postgres, FastAPI skeleton, React skeleton, all started by `docker-compose up`
4. FastAPI init: health endpoint that checks real database connectivity, SQLAlchemy
   connection, first pytest test
5. Alembic baseline migration: `teams`, `players`, `games`, plus 32-team seed script
6. React scaffold: Vite and Tailwind, one page calling the health endpoint

### Phase 2: Import pipeline and data
7. Folder layout under `data/`, a header-only template and sidecar template for every
   `table_key`, and `data/README.md` (done; update it for Path C in step 12)
8. Cleanup of leftover old-plan files, then Alembic migration: `data_snapshots`,
   `team_offense_stats`, `team_defense_stats`
9. Import script: reads one CSV plus its sidecar, maps team names, validates row count
   and ranges, flags stale or disagreeing sources, upserts by team + season +
   `as_of_week`, writes a `data_snapshots` row, logs the run; pytest with small fixture CSVs
10. Import the Week 6 screenshot CSVs (through Week 5); George spot-checks three rows per
    table before the import runs
11. Backend endpoints: `/teams/{id}/offense`, `/teams/{id}/defense`, filterable by season and as-of week; tests
12. nflverse loader, part 1: schedules, scores, scoring plays, historical spreads, totals
    and moneylines, weather, 2018 on, into `games`, `game_lines` (closing),
    `game_scoring_plays`; migration for these tables; update `data/README.md` for Path C; tests
13. nflverse loader, part 2: aggregate play-by-play and participation data into
    `game_team_stats`, team-week features, and `player_weekly_stats` for 2018 on, with
    the as_of_week rule and leakage tests
14. Overlap test: rebuild 2026 through Week 5 from nflverse and compare with the Week 6
    screenshots for all 32 teams (correlation and average gap per stat, plus the 9,597
    charted-plays checksum for personnel). Writes the committed `feature_sources`
    config saying which stats read nflverse and which stay screenshot-only
15. Data checklist endpoint: `/data/checklist?season=&week=` returns each catalog
    table as loaded, missing, or optional for that week
16. Frontend "Data Checklist" tab: shows what is loaded and what is still missing for the week

### Phase 3: Game model (spread, moneyline, total)
17. Simulator core and v0 game model: team ratings from hand-set weights, expected
    points per team, margin, win probability and total, manual QB-out flag; writes locked
    rows to `model_predictions`; run logging; tests
18. De-vig utility (same-percentage method, raw prices kept) and the market-only
    benchmark: the market's log loss and calibration on 2021 on
19. Backtest runner: walk-forward on nflverse seasons, recency weights, log loss and
    calibration against the market; compare 2018-on and 2021-on training; writes
    `backtest_runs`
20. Live game lines: current spread and total (nflverse if updated, else chat CSV),
    moneylines and closing lines from sportsbook screenshots; source on every row; tests
21. Settle script: after scores import, mark each locked prediction hit, miss, or push
    and fill `prediction_results`; stores closing price and CLV; logs hit rate and bucket breakdown
22. Hit-rate, calibration and CLV endpoint: `/models/game/calibration`; tests
23. Frontend: team stats table, and a hit-rate and calibration chart with a clear
    "small sample" note until enough games settle
24. Weekly results endpoint: `/models/game/weekly-results?week=&season=`
25. Frontend "Weekly Check" tab: every game of the selected week, predicted spread,
    total and winner against the actual result, outcome marked, "pending" for unplayed games
26. Edge endpoint: `/games/{id}/edge` with shrinkage, minimum edges, and the +300 price cap; tests
27. Frontend "Value Board": ranked edges by bet type, labeled with model version and sample size
28. Learned v1: ridge regression on the backfill (2021 on, recency weighted), compared with
    v0 and the market in the backtest and in forward grading; boosted trees only if
    they beat ridge on held-out seasons

### Phase 4: Player props
29. Migration for `player_prop_lines`, `tracked_players`, and prediction columns for props
30. Tracked players: pick the top 20 by target share each week; templates for sportsbook
    prop screenshots (about 8 images a week)
31. Prop lines and anytime-TD odds import (four markets, tracked players only); tests
32. Receiving props v0: targets x catch rate x yards per catch, negative binomial and
    gamma, driven by the game simulator; opponent adjustment by position; run logging
33. Settle receiving props from nflverse player results; pushes handled; hit-rate and CLV tracking
34. Frontend: player prop comparison view
35. Rushing props v0: carries x yards per carry from the simulator's game script; run logging
36. Settle rushing props
37. Extend the Weekly Check tab to props (predicted against actual stat line, outcome, filter by prop type)
38. Learned player model v1: hierarchical or boosted model on 2021 on, beside v0
39. (Optional) Odds API check: confirm the free plan includes the markets needed and
    the credit cost; if yes, add a script that writes the same CSV and sidecar format,
    with the key in `.env`

### Phase 5: Anytime TD model
40. Red zone and goal-line features from nflverse play-by-play (team and player)
41. TD model v0: team touchdown pool from expected points, divided by red zone and
    goal-line share; P(TD) = 1 - e^(-rate); run logging
42. Settle the TD model from `game_scoring_plays`; hit-rate, calibration and CLV tracking
43. Frontend: TD probability leaderboard
44. Extend the Weekly Check tab to anytime-TD picks (did the player score, yes or no)
45. Learned TD v1: logistic or boosted model on 2021 on, beside v0

### Phase 6: Polish, CI/CD, deployment
46. GitHub Actions: run the full pytest suite on every PR
47. Demo mode: seed synthetic sample data so a public deployment and README screenshots never show third-party numbers
48. Deploy to Render or Railway: Postgres, backend, frontend live; secrets set in the host's secret manager
49. README and architecture doc, with the current hit rate and CLV for each model type near the top
50. (Optional, later) Auth: `users` table and saved picks, only when opening this to other people

---

## Interview Talking Points

- Why nflverse first and screenshots only for the gaps, and how the overlap test decided which was which instead of assuming the sources matched.
- Why one simulator drives every bet, so a spread, a total and a player prop for the same game never contradict each other.
- Why the benchmark is the market's closing price, and why closing line value is tracked from week one.
- Why `as_of_week` exists and how leakage tests enforce it, including on the rebuilt history. Leakage is the most common way sports models look better than they are.
- Why validation has two layers: a walk-forward backtest on history, and forward grading of locked picks at real prices.
- Why models start as baselines, and how the training window (2021 on, recency weighted) was tested against a longer one.
- Why the anytime-TD model fixes the team's touchdown pool first and then divides it.
- A specific hit-rate, calibration or CLV result once weeks have accumulated, with the sample size stated.
- Why predictions are locked and timestamped before kickoff, and why pushes and ties are settled explicitly.
- Why tests were written with each module, and the difference between a correctness test and a calibration check.
- Why every import and model run writes a structured log, separate from the Postgres tables.
- Why the schema keeps predictions user-agnostic, so auth can be added later without a rewrite.
- Why the public demo runs on synthetic data.
