# NFL Prediction Platform: Project Spec (v3, screenshots plus chat-pulled CSVs)

## Purpose

Personal project, built to be usable by other people later and to bring up in
software engineering interviews as a full-stack, self-directed build. Framed publicly
as a predictive analytics platform with a market-comparison feature, covering game
outcomes, player props, and anytime-TD probability.

Scope: pregame only. Predictions are made and locked before kickoff from season-to-date
stats and the posted lines. Nothing is tracked during a game. After games, only three
kinds of final results come in: final scores (ties are ties), each tracked player's
final line (receiving yards, rushing yards, receptions, TDs), and the bet results
(closing lines and whether each spread, total, moneyline, and prop hit or pushed).

Data approach: the app has no scrapers, no scheduled fetching, and no API keys. Every
number reaches Postgres through one importer, from one of two paths. Path A is a
screenshot that George captures and Claude Code transcribes to CSV. Path B is a CSV
that Claude writes in a separate chat after reading a public page or a sports data
feed on request. Both paths produce the same CSV format and go through the same
validation.

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

## Data approach: two paths, one importer

### The pipeline

```
Path A  Screenshot (George) -> data/inbox/season-YYYY/week-NN/<table_key>.png
          -> Claude Code reads the image, writes data/extracted/.../<table_key>.csv
          -> George spot-checks 3 rows per table

Path B  Claude chat reads a public page or sports feed on request
          -> writes <table_key>.csv into the chat; George saves it to data/extracted/...

Both    -> <table_key>.meta.json sidecar sits next to the CSV
        -> import script validates and upserts into Postgres
        -> data_snapshots row records what was loaded, when, and from where
```

Path B runs only when George asks, one page at a time. Claude Code never fetches
anything itself.

### The sidecar file

Every CSV has a `<table_key>.meta.json` beside it with: `source_name`, `source_url`
(or "screenshot"), `captured_at`, `as_of_week`, `target_week`. The importer refuses a
CSV that has no sidecar.

### Rules for transcription (Path A)

- Copy values exactly as shown. Every row, no rounding, no estimating.
- If a value is cut off or unreadable, leave it blank and list it in the reply. Never
  fill a gap from memory or from a neighboring row.
- Report the row count. A team table should have 32 rows (28 to 30 in a bye week for
  per-game views). Flag duplicates, missing teams, and implausible values.
- Team names arrive as full names ("Los Angeles Rams"), nicknames ("Rams"), or
  abbreviations ("LAR", or "LA" in some feeds). The importer maps all of them to the
  `teams` table and rejects anything it cannot match.

### Rules for fetched data (Path B)

- Fetched pages can disagree with themselves or lag. One odds page showed two
  different totals for the same game; a red zone page stopped at Week 4 during Week 6.
  The CSV keeps what the source showed, with `captured_at`.
- The importer flags a source that is behind `as_of_week`, and a spread or total that
  differs by more than 1 point between two sources for the same game.
- Moneyline prices come from sportsbook screenshots only. The odds page tested showed
  percentages, not prices.
- Ties are real results. A 27-27 game is stored as a tie, and standings feeds that drop
  ties are not used as a source of record.
- Only final scores are kept from a feed. Per-quarter splits, box scores, and play-by-play
  are not used.

### Snapshot semantics (this prevents leaking the future into a prediction)

Season-to-date tables describe games already played. A capture made on Tuesday of
Week 6 says "through Week 5" and is used to predict Week 6 games. Every import records
`as_of_week` (5) and `target_week` (6). A model may only read rows whose `as_of_week`
is strictly before the week it is predicting. Overwriting last week's numbers with this
week's destroys history, so rows are keyed by team + season + `as_of_week` and never
edited in place.

### Weekly routine

1. After Monday Night Football, capture the team-stat screenshots (about 12) into
   `data/inbox/season-2026/week-NN/`, one file per table, named with the `table_key`.
2. In a Claude chat, ask for the week's schedule, final scores for finished games,
   spreads and totals, and the red zone pages. Save each CSV and
   sidecar it writes into `data/extracted/season-2026/week-NN/`.
3. Screenshot sportsbook moneylines and four prop markets (receiving yards, receptions,
   rushing yards, anytime TD) for tracked players.
4. After the games, screenshot the weekly stat leader pages (receiving yards, rushing
   yards, receptions) and the TD scorers list to settle yardage props and anytime-TD
   picks.
5. Ask Claude Code to transcribe the screenshots and import everything. The Data
   Checklist tab shows what is still missing.

### Source terms and privacy

Every source has its own terms, and they differ.

- The charted stats come from a paid analytics site. Its terms prohibit automated
  access and also prohibit copying material, by hand or by script, without written
  consent. Email the site and ask whether personal, non-commercial use of the tables in
  this project is allowed. Until there is a written yes, treat that source as private.
- Public odds pages, the red zone pages, and the sports feed are read one page at a
  time on request. Check each site's terms before making any of it routine.
- Sportsbook screenshots are for personal use.

Until permissions are settled:

- Raw screenshots and extracted CSVs stay out of git (`data/inbox/` and
  `data/extracted/` are in `.gitignore`).
- Nothing from a restricted source appears in a public deployment or in README
  screenshots. The public demo runs on synthetic sample data (see the demo-mode step).

---

## Data catalog (the checklist)

Path A is a screenshot, Path B is a chat-pulled CSV. Status reflects the Week 6
capture folder (stats through Week 5) plus what was tested in chat. "Have" means a
usable screenshot exists. Five files in that folder were duplicates and one was blank,
so they add nothing.

### Team stats from the charting source

| table_key | Columns | Path | Status |
|---|---|---|---|
| team_offense_metrics | EPA/play, yards/play, points/drive, explosive play rate, down conversion rate | A | Have |
| team_defense_metrics | Same five, allowed | A | Have |
| offensive_tendencies | Motion rate, play action rate, air yards/att, shotgun rate, no-huddle rate | A | Have |
| offensive_pace_volume | Plays/game, adjusted plays/game, plays/drive, drives/game, time of possession | A | Have |
| offensive_pace_tempo | Play clock used, neutral play clock, neutral pass rate, gear change, no-huddle % | A | Missing |
| personnel_usage | 11, 12, 13, 21, 22, 2+TE, 2+RB, 3+WR, plays | A | Have |
| personnel_efficiency | EPA/play, yards/play, success rate by grouping | A | Optional |
| offensive_line | Pressure rate allowed, no-blitz pressure allowed, time to throw, yards before contact per RB rush, rush stuff rate | A | Have |
| defensive_line | Pressure rate, no-blitz pressure rate, yards before contact per RB rush, rush stuff rate | A | Have |
| defensive_tendencies | Blitz rate, light box rate, heavy box rate, sub package rate | A | Have |
| man_zone_coverage | Man rate, zone rate, middle closed rate, middle open rate | A | Have |
| positional_coverage | Yards per target allowed to WR, TE, RB, outside, slot | A | Have |
| proe_matchups | Offense PROE, defense PROE, matchup score, for the target week | A | Have (Week 6) |
| proe_offense_defense | Season-to-date PROE for all 32 teams (Offense and Defense tabs) | A | Optional |

### Schedule and final scores

| table_key | Columns | Path | Status |
|---|---|---|---|
| weekly_matchups | Teams, home/away, kickoff for the target week | B (sports feed) | Chat-pulled |
| game_results | Final score per game, including ties | B (sports feed) | Chat-pulled |

### Betting data

| table_key | Columns | Path | Status |
|---|---|---|---|
| game_lines (spread, total) | Spread and total per game, with capture time | B (public odds page) | Chat-pulled |
| game_lines (moneyline) | Moneyline prices both sides | A (sportsbook) | Missing |
| game_lines_closing | Spread, total, moneyline shortly before kickoff, for judging edge against the close | A | Missing |
| player_prop_lines | Player, prop type, line, over price, under price (receiving yards, receptions, rushing yards) | A (sportsbook) | Missing |
| anytime_td_odds | Player, price | A (sportsbook) | Missing |

### Player data

| table_key | Columns | Path | Status |
|---|---|---|---|
| player_usage | Snap share, targets, target share, air yards, carries, rushing and receiving yards, TDs | A (league-wide pages) | Missing |
| player_red_zone | Red zone targets, rushes, and receptions per player, by week | B (one page per team) | Chat-pulled (tested for one team) |
| player_results | Weekly receiving yards, rushing yards, receptions, TDs for tracked players | A (weekly stat leaders, TD scorers) | Missing |
| team_red_zone | Red zone TD rate, offense and allowed | B (red zone pages) | Optional |
| injuries | Out, doubtful, and questionable players for the week | A | Optional |

Minimum to get the game model running: everything marked Have, plus
offensive_pace_tempo, weekly_matchups, game_results, and game_lines. Player and TD
tables are only needed once Phase 4 starts.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Frontend | React (JavaScript), Tailwind CSS, Recharts |
| Backend | Python, FastAPI, SQLAlchemy, Alembic (migrations) |
| Database | PostgreSQL |
| Data handling | pandas (CSV validation and feature building) |
| Modeling | scikit-learn, XGBoost (after enough data exists, see Validation) |
| Data entry | Path A screenshots and Path B chat-pulled CSVs, both loaded by one import script |
| Deployment | Docker Compose (local), Render or Railway (hosted) |
| CI/CD | GitHub Actions, added once the core app works |

Removed from v2: nfl_data_py. The Odds API is not used, and stays on the table as an
optional step (step 32) once its free plan is confirmed to cover the markets needed.
The importer is source-agnostic and tags every snapshot with its source, so a bulk
historical loader or an odds script can be added later without changing the schema.

One Python codebase, no separate services. FastAPI serves requests; import and model
modules run as scripts.

---

## Architecture

```
Screenshots -> CSV (Claude Code) --+
                                   +-> sidecar + import script -> Postgres <- FastAPI <- React
Chat-pulled CSV (Claude chat) -----+                                ^
                         Model modules (game / prop / TD) write predictions here
```

---

## Validation strategy: forward only

Captures reflect the present, so a completed season cannot be replayed. Instead:

- Every prediction is written and timestamped before kickoff, then locked.
- Results are imported after games finish and each prediction is settled as hit, miss,
  or push (a spread that lands on the number, or a tie on a moneyline).
- Hit rate and calibration build up week by week. With about 14 games a week, expect
  a calibration chart to be noisy for the first month. Say so on the page.
- Training data does not exist on day one. Each model starts as a baseline with
  documented, hand-set weights (version `v0`). A learned model (XGBoost or
  regression) gets trained once at least 50 settled games with valid snapshots exist,
  then runs next to the baseline. Compare them week by week on games neither saw.
- The week 1 to 5 results already played cannot be used to train, because only
  through-Week-5 stats exist for them. Treat them as context, not training rows.

---

## Model types (three distinct problems, built in this order)

1. Game outcome: predicted margin and win probability from offense and defense EPA,
   pace, personnel, pressure and coverage tendencies, and PROE. Baseline v0 first,
   learned v1 later.
2. Player prop over/under: expected stat line (yards, receptions) compared against
   the posted line. Baseline v0 is a usage-weighted rolling average adjusted by the
   opponent's positional yards-per-target allowed.
3. Anytime TD scorer: a probability problem, not a regression. Logistic or Poisson
   approach on red zone usage, goal-line share, and opponent TD rate allowed.

Each model type has its own module, its own `model_version`, and its own rows in the
results tables. They are never blended into one score.

---

## Database Schema

Core entities
- `teams`: id, name, nickname, abbreviation, conference, division
- `players`: id, name, team_id, position
- `games`: id, season, week, home_team_id, away_team_id, kickoff_time, status
  (scheduled, final), home_score, away_score. Ties are stored as ties.

Import tracking
- `data_snapshots`: id, season, as_of_week, target_week, table_key, source_name,
  source_url, captured_at, file_name, row_count, status (loaded, partial, rejected), notes

Team stats (wide tables, one row per team + season + as_of_week, each import fills
its own columns by upsert)
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
  ypt_allowed_slot, def_proe, rz_td_rate_allowed

Betting data
- `game_lines`: game_id, line_type (open, current, closing), spread_home, moneyline_home,
  moneyline_away, total, captured_at, source_name, book_label. Moneyline columns are
  nullable, because spread and total can arrive from a different source than prices.
- `player_prop_lines`: player_id, game_id, prop_type, line, over_price, under_price,
  captured_at, book_label
- `line_results`: game_line_id or player_prop_line_id, market (spread, total, moneyline,
  prop), outcome (home_cover, away_cover, over, under, home_win, away_win, push),
  settled_at. Worked out from final scores and player finals, never from in-game data.

Player data
- `player_weekly_stats`: player_id, season, week, snaps, snap_share, targets,
  target_share, carries, red_zone_touches, red_zone_targets, goal_line_carries,
  air_yards, receiving_yards, rushing_yards, receptions, tds

Predictions and validation
- `model_predictions`: game_id, model_type (game, prop, td), model_version,
  predicted_value, predicted_prob, snapshot_as_of_week, locked_at
- `player_predictions`: player_id, game_id, prop_type, model_version, predicted_value,
  predicted_prob, snapshot_as_of_week, locked_at
- `prediction_results`: prediction_id, prediction_table, actual_value, outcome (hit,
  miss, push), confidence_bucket, settled_at

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
- The CSV importer gets the heaviest tests: sidecar required, team name mapping, row
  count checks, range checks, duplicate handling, upsert behavior, `as_of_week` rules,
  and the stale-source and source-disagreement flags.
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

### Phase 1: Foundation
1. Repo scaffold: `/frontend`, `/backend`, `/data`, root README, `.gitignore`
   (including `.env`, `CLAUDE.md`, `data/inbox/`, `data/extracted/`)
2. `.env.example` and `.env` setup, config loading in the backend (`pydantic-settings`)
3. Docker Compose: Postgres, FastAPI skeleton, React skeleton, all started by `docker-compose up`
4. FastAPI init: health endpoint that checks real database connectivity, SQLAlchemy
   connection, first pytest test
5. Alembic baseline migration: `teams`, `players`, `games`, plus 32-team seed script
6. React scaffold: Vite and Tailwind, one page calling the health endpoint

### Phase 2: Import pipeline
7. Folder layout under `data/`, a header-only CSV template and sidecar template for
   every `table_key` in the catalog, and `data/README.md` describing both paths, the
   weekly routine, and the transcription rules
8. Alembic migration: `data_snapshots`, `team_offense_stats`, `team_defense_stats`
9. Import script: reads one CSV plus its sidecar, maps team names, validates row count
   and ranges, flags stale or disagreeing sources, upserts by team + season +
   `as_of_week`, writes a `data_snapshots` row, logs the run; pytest with small fixture CSVs
10. Transcribe and import the Week 6 screenshot set (through Week 5) into CSVs;
    George spot-checks three rows per table before the import runs
11. Backend endpoints: `/teams/{id}/offense`, `/teams/{id}/defense`, filterable by season and as-of week; tests
12. Schedule and final scores: migration adding `games.status` and final scores
    (kickoff nullable); importer for weekly_matchups and game_results from chat-pulled
    CSVs; ties stored as ties; tests
13. Data checklist endpoint: `/data/checklist?season=&week=` returns each catalog
    table as loaded, missing, or optional for that week
14. Frontend "Data Checklist" tab: shows what is loaded and what is still missing for the week

### Phase 3: Game outcome model and forward validation
15. Baseline model v0: predicted margin and win probability from the matchup (offense
    metrics against opposing defense metrics, pace, PROE), hand-set weights documented
    in code; writes locked rows to `model_predictions`; run logging
16. Game lines import: spreads and totals from chat-pulled CSVs, moneylines and closing
    lines (captured shortly before kickoff) from sportsbook screenshots; source recorded
    on every row; tests
17. Settle script: after final scores import, mark each locked prediction hit, miss, or
    push and fill `prediction_results`, and record each game line's bet result (cover,
    over/under, moneyline, push) in `line_results`; logs hit rate and bucket breakdown
18. Hit-rate and calibration endpoint: `/models/game/calibration`; tests
19. Frontend: team stats table, and a hit-rate and calibration chart with a clear
    "small sample" note until enough games settle
20. Weekly results endpoint: `/models/game/weekly-results?week=&season=` returns each
    game's prediction beside the actual result, with outcome
21. Frontend "Weekly Check" tab: every game of the selected week, predicted spread and
    winner against the actual result, outcome marked, "pending" for unplayed games
22. Edge endpoint: `/games/{id}/edge` compares model probability with the line's
    implied probability; tests
23. Frontend "Value Board": ranked games by model-versus-market edge
24. Learned model v1: once 50 or more games are settled with valid snapshots, train a
    regression or XGBoost model, run it beside v0, compare weekly on unseen games

### Phase 4: Player props
25. Player CSV templates and migration for `player_weekly_stats` and `player_prop_lines`
26. Player usage import from league-wide screenshots (snap share, targets, carries);
    pick about 40 tracked players per week by usage; logging and tests
27. Prop lines and anytime-TD odds import (four markets, tracked players only); tests
28. Baseline prop model v0 (usage-weighted rolling average adjusted by opponent positional YPT); run logging
29. Settle props: yardage from weekly stat leader screenshots into `player_weekly_stats`,
    TDs from `player_results`; prop bet results into `line_results`; pushes handled;
    hit-rate tracking, same pattern as Phase 3
30. Frontend: player prop comparison view
31. Extend the Weekly Check tab to props (predicted against actual stat line, outcome, filter by prop type)
32. (Optional) Odds API check: confirm the free plan includes the markets needed and
    the credit cost; if yes, add a script that writes the same CSV and sidecar format,
    with the key in `.env`

### Phase 5: Anytime TD model
33. Red zone import: player red zone usage from the per-team pages as chat-pulled CSVs
    (32 teams), plus team red zone rates from the same pages where available
34. Baseline TD model v0 (logistic or Poisson on red zone usage and opponent TD rate allowed); run logging
35. Settle the TD model from `player_results` TDs; hit-rate tracking
36. Frontend: TD probability leaderboard
37. Extend the Weekly Check tab to anytime-TD picks (did the player score, yes or no)

### Phase 6: Polish, CI/CD, deployment
38. GitHub Actions: run the full pytest suite on every PR
39. Demo mode: seed synthetic sample data so a public deployment and README screenshots never show third-party numbers
40. Deploy to Render or Railway: Postgres, backend, frontend live; secrets set in the host's secret manager
41. README and architecture doc, with the current hit rate for each model type near the top
42. (Optional, later) Auth: `users` table and saved picks, only when opening this to other people

---

## Interview Talking Points

- Why the data enters through one importer from two paths: no free key-based API covered the charted stats, and some sources' terms ruled out scraping. Every CSV carries a sidecar with source and capture time, and the importer flags stale or disagreeing sources.
- A real example of why validation matters: one odds page showed two different totals for the same game, and a red zone page lagged by two weeks.
- Why `as_of_week` exists: a prediction may only see data that existed before the games it predicts. Leakage is the most common way sports models look better than they are.
- Why validation is forward-only, and why models start as baselines and get trained once 50 settled games exist.
- Why three separate model types instead of one blended score (regression, regression against a posted line, classification).
- A specific hit-rate or calibration result once weeks have accumulated, with the sample size stated.
- Why predictions are locked and timestamped before kickoff, and why pushes and ties are settled explicitly.
- Why tests were written with each module, and the difference between a correctness test and a calibration check.
- Why every import and model run writes a structured log, separate from the Postgres tables.
- Why the schema keeps predictions user-agnostic, so auth can be added later without a rewrite.
- Why the public demo runs on synthetic data.
