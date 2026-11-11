# Data

Every number in the app reaches Postgres through one import layer, from one of three
paths. There are no scrapers, no scheduled fetching, and no API keys.

- **Path C (nflverse, first choice):** George downloads nflverse files with nflreadr in R
  and saves them under `extracted/nflverse/`. Used for anything nflverse provides.
- **Path A (screenshot):** George captures a screenshot, Claude Code transcribes it to CSV.
  Used only for what nflverse lacks (prop lines, anytime-TD prices, live moneylines,
  slot and outside yards per target, play clock stats, gear change).
- **Path B (chat-pulled CSV):** In a separate Claude chat, George asks for one public
  page or sports feed at a time; the chat writes a CSV and sidecar, and George saves them.
  A fallback for the live week's schedule, scores, spreads and totals.

Every file has a sidecar and goes through the same validation. A stat is not read from
two sources interchangeably until the overlap test (spec step 14) shows they match.

The models predict before kickoff. After the games, nflverse supplies the results that
grade them: final scores, closing lines, scoring plays and player stat lines. No live
in-game data is kept.

## Folder layout

```
data/
  README.md              this file
  templates/             header-only CSV + sidecar for every table_key (committed)
  inbox/                 Path A screenshots (gitignored)
    season-2026/
      week-06/
        team_offense_metrics.png
  extracted/             files + sidecars from every path (gitignored)
    season-2026/
      week-06/
        team_offense_metrics.csv
        team_offense_metrics.meta.json
    nflverse/              Path C
      DOWNLOAD_INFO.txt    when and with which nflreadr version George downloaded
      schedules/
        2026/
          schedules.csv.gz
          schedules.meta.json
      play_by_play/2026/...
```

- Files are named by `table_key`. A table split across several screenshots gets a
  suffix: `positional_coverage-1.png`, `positional_coverage-2.png`. The CSV is still one file.
- `week-NN` is the **target week**: the week the data is used to predict. The Week 6
  folder holds stats through Week 5.
- The season comes from the `season-YYYY` folder name.
- `inbox/` and `extracted/` stay out of git until source permissions are settled (see
  Source terms below).

## The sidecar

Every CSV has a `<table_key>.meta.json` next to it. The importer refuses a CSV without one.

```json
{
  "source_name": "charting site, offense page",
  "source_url": "screenshot",
  "captured_at": "2026-10-13T21:40:00-04:00",
  "as_of_week": 5,
  "target_week": 6
}
```

| Field | Meaning |
|---|---|
| `source_name` | Where the numbers came from, in words |
| `source_url` | The page URL for Path B; the literal `"screenshot"` for Path A |
| `captured_at` | When the screenshot was taken or the page was read, ISO 8601 with offset |
| `as_of_week` | Last week of games the numbers include |
| `target_week` | The week these numbers are used to predict |

A model reads only snapshots whose `as_of_week` is strictly before the week it
predicts. Rows are keyed by team + season + `as_of_week` and never edited in place, so
last week's numbers survive when this week's arrive.

## nflverse files (Path C)

George downloads with nflreadr in R, one season per file, into
`extracted/nflverse/<dataset>/<season>/<dataset>.csv.gz`, and records each download in
`DOWNLOAD_INFO.txt`. Claude Code never downloads them.

Sidecars for these files come from `DOWNLOAD_INFO.txt`, not from memory:

```
python -m app.nflverse_loader sidecars data/extracted/nflverse
```

This writes `<dataset>.meta.json` next to each file that lacks one and never overwrites
one. `source_name` names the dataset and nflreadr version; `source_url` is the nflreadr
call, e.g. `nflreadr::load_schedules(2026)`. `as_of_week` is the last week the file
covers in full: a Thursday final doesn't count until the rest of that week is played.
A download time written without an offset is read as US Eastern.

```
python -m app.nflverse_loader load data/extracted/nflverse            (every season)
python -m app.nflverse_loader load data/extracted/nflverse --season 2026
```

| Dataset | Loads into | Notes |
|---|---|---|
| schedules | `games`, `game_lines` | Final scores (ties kept), game type, neutral site, roof, temp, wind. A played game's line is stored as `closing`, captured at kickoff; an upcoming game's line as `current`, captured at the download time |
| play_by_play | `game_scoring_plays` | Touchdowns (pass, rush, other), field goals, extra points, two-point conversions, safeties, with the scorer |

Loader rules:

- Schedules load before play-by-play, because scoring plays attach to games.
- Prices are raw American odds. `spread_home` is the home spread as a sportsbook prints
  it (-3.5 = home favored); nflverse's `spread_line` has the opposite sign.
- A final from the sidecar's target week (a Thursday game) loads, and is flagged so no
  model predicting that week reads it. A final after the target week is rejected.
- A game's scoring plays should add up to its final score. When they don't, the game is
  flagged and the final score from schedules stands. In 2018–2026 that is 3 of 2,499
  games.
- A season with fewer regular-season games than a full schedule is flagged (2022 has 271:
  Bills at Bengals was cancelled).
- Older seasons use OAK for the Raiders; it maps to the current franchise.
- Re-importing updates games and lines in place and replaces a game's scoring plays.
- `injuries` is not loaded yet.

### Built stats (step 13)

After the loader, the builder turns play-by-play, participation, rosters, player stats
and snap counts into the tables the models read:

```
python -m app.nflverse_build data/extracted/nflverse            (every season)
python -m app.nflverse_build data/extracted/nflverse --season 2026
```

| Builds | From | Notes |
|---|---|---|
| `team_offense_stats`, `team_defense_stats` with `source = nflverse` | play_by_play, pbp_participation, rosters | One row per team per `as_of_week`, season to date. The row for week w uses games of weeks 1..w only |
| `game_team_stats` | play_by_play, pbp_participation | Each team's box score per game |
| `players`, `player_weekly_stats` | rosters, player_stats, snap_counts, play_by_play | QB, RB, WR and TE lines; red zone and goal-line usage come from play-by-play |

Builder rules:

- Each file needs its sidecar. Play-by-play is required; a season without a usable
  participation file still builds, with personnel, box, pass rush, pressure, time to
  throw and coverage left empty, and says so. 2026 participation isn't published yet.
- Weeks after a file's `as_of_week` are left out, so a partly played week never enters a
  season-to-date row.
- Rates are season sums divided by season sums, never averages of weekly averages.
- Screenshot rows are never touched. A rebuild replaces only that season's nflverse rows.
- Definitions (the overlap test in step 14 checks them against the screenshots):
  explosive is a designed run of 10+ or a dropback of 20+ yards; neutral is win
  probability 20-80% outside the last two minutes of a half; down conversion is the
  share of series that earn a first down or TD; blitz is 5+ pass rushers; light box is
  6 or fewer, heavy 8 or more; sub package is 5+ defensive backs; middle closed is
  Cover 1 or 3, middle open is Cover 2, 2-man, Cover 4 or 6.
- Not built from nflverse (screenshot only): motion rate, play action rate, adjusted
  plays, play clock stats, gear change, yards before contact, slot and outside yards
  per target.
- Snap counts match by Pro Football Reference id, or by name, team and game when the
  roster lacks the id and the name is unique. Lines still unmatched are counted.

### Overlap test (step 14)

Before a model reads a team stat, the overlap test decides which source it comes from:

```
python -m app.overlap --season 2026 --as-of-week 5 \
    --personnel-csv data/extracted/season-2026/week-06/personnel_usage.csv --write
```

It pairs each team's screenshot row with its nflverse row for that week and, per stat,
reports the correlation across teams, the average gap and the bias (nflverse minus
screenshot). A correlation above 0.9 with at least 20 paired teams reads nflverse;
anything else stays on the screenshot. It also sums the screenshot's charted plays
against nflverse's offensive plays as a checksum. The decisions are written to
`backend/config/feature_sources.json`, which is committed. It holds only correlations
and gaps, no team values. Models read a stat's source through
`app.features.feature_source`.

Rerun it when a new screenshot week or nflverse download could change a decision, for
example once 2026 participation data is published.

## Templates

`templates/` has a header-only `<table_key>.csv` and a blank `<table_key>.meta.json`
for each table below. Copy both into the week's `extracted/` folder and fill them in.
Column names match the database columns where one exists.

Season-to-date team tables have one row per team and take their week from the sidecar.
Game and player tables have a `week` column, because a folder can hold last week's
results next to this week's lines.

| table_key | Path | One row per | Notes |
|---|---|---|---|
| team_offense_metrics | A | team | |
| team_defense_metrics | A | team | |
| offensive_tendencies | A | team | |
| offensive_pace_volume | A | team | |
| offensive_pace_tempo | A | team | |
| personnel_usage | A | team | |
| personnel_efficiency | A | team + personnel grouping | Optional |
| offensive_line | A | team | |
| defensive_line | A | team | |
| defensive_tendencies | A | team | |
| man_zone_coverage | A | team | |
| positional_coverage | A | team | |
| proe_matchups | A | offense vs defense pairing | Target week only |
| proe_offense_defense | A | team | Optional |
| weekly_matchups | B | game | Fallback for nflverse schedules |
| game_results | B | game | Final scores only. Ties are stored as ties. Fallback for nflverse schedules |
| game_lines_spread_total | B | game | Public odds page |
| game_lines_moneyline | A | game | Sportsbook only |
| game_lines_closing | A | game | Per-row `captured_at`, since each game closes at a different time |
| player_prop_lines | A | player + prop type | Tracked players only |
| anytime_td_odds | A | player | |
| player_usage | A | player | |
| player_red_zone | B | player | One page per team |
| team_red_zone | B | team | Optional, from the red zone pages |
| player_results | A | player | Weekly stat leaders and TD scorers; settles props and anytime-TD picks |
| injuries | A | player | Optional |

Schedule and results rules:

- A game is identified by season, `week`, away team, and home team. A results row with
  home and away swapped against the stored schedule is rejected, not guessed.
- A results file can only hold games up to its sidecar's `as_of_week`, so a final score
  never sits in a snapshot that predicts that same week.
- Re-importing a schedule updates kickoff times (flexed games). Re-importing a result with
  a different score is flagged.

## Transcription rules (Path A)

- Copy values exactly as shown. Every row, no rounding, no estimating. Keep `%` signs,
  `MM:SS` times and `+`/`-` on prices as they appear; the importer parses them.
- Team names go in as shown: full name ("Los Angeles Rams"), nickname ("Rams"), or
  abbreviation ("LAR", or "LA" in some feeds). The importer maps them to the `teams`
  table and rejects anything it cannot match.
- If a value is cut off or unreadable, leave the cell blank and list it in the reply.
  Never fill a gap from memory or from a neighboring row.
- Report the row count. A team table has 32 rows (28 to 30 for per-game views in a bye
  week). Flag duplicate teams, missing teams, and implausible values.
- George spot-checks 3 rows per table before the import runs.

## Rules for chat-pulled data (Path B)

- The CSV keeps what the source showed, even when it looks wrong. Problems get reported,
  not edited.
- Pages can lag or disagree with themselves. The importer flags a source that is behind
  `as_of_week`, and a spread or total that differs by more than 1 point between two
  sources for the same game.
- Moneyline prices come from sportsbook screenshots only.
- Standings feeds that drop ties are not a source of record.
- Only final scores are kept from a feed. Its per-quarter splits are not trusted;
  scoring plays and box-score stats come from nflverse play-by-play.

## Weekly routine

1. After Monday Night Football, download the updated 2026 nflverse files into
   `extracted/nflverse/`, add a line to `DOWNLOAD_INFO.txt`, delete the 2026 sidecars
   for the files you replaced, and run `sidecars` then `load --season 2026`.
2. Screenshot only what nflverse lacks into `inbox/season-2026/week-NN/`, one file per
   table, named by `table_key`: sportsbook moneylines, four prop markets (receiving
   yards, receptions, rushing yards, anytime TD) for tracked players, and any team stat
   the overlap test kept on screenshots.
3. If nflverse hasn't updated the schedule, scores, spreads or totals yet, pull them in a
   Claude chat and save the CSV and sidecar into `extracted/season-2026/week-NN/`.
4. Ask Claude Code to transcribe the screenshots and import everything. The Data
   Checklist tab shows what is still missing.

## Source terms

- nflverse is open data. Check its license and attribution notes before publishing any
  of it.
- The charted stats come from a paid analytics site whose terms prohibit automated
  access and copying without written consent. Treat it as private until there is a
  written yes for personal, non-commercial use.
- Public odds pages, red zone pages, and the sports feed are read one page at a time on
  request. Check each site's terms before making any of it routine.
- Sportsbook screenshots are for personal use.
- Nothing from a restricted source goes into a public deployment or README screenshots.
  The public demo runs on synthetic data.
