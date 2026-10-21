# Data

Every number in the app reaches Postgres through one import script, from one of two
paths. There are no scrapers, no scheduled fetching, and no API keys.

- **Path A (screenshot):** George captures a screenshot, Claude Code transcribes it to CSV.
- **Path B (chat-pulled CSV):** In a separate Claude chat, George asks for one public
  page or sports feed at a time; the chat writes a CSV and sidecar, and George saves them.

Both paths produce the same CSV format and go through the same validation.

Everything here is pregame data, plus final results after the games: final scores, each
tracked player's final line, and the closing lines used to grade bets. No box scores,
scoring plays, or in-game data are kept.

## Folder layout

```
data/
  README.md              this file
  templates/             header-only CSV + sidecar for every table_key (committed)
  inbox/                 Path A screenshots (gitignored)
    season-2026/
      week-06/
        team_offense_metrics.png
  extracted/             CSVs + sidecars, from either path (gitignored)
    season-2026/
      week-06/
        team_offense_metrics.csv
        team_offense_metrics.meta.json
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
| weekly_matchups | B | game | |
| game_results | B | game | Final scores only. Ties are stored as ties |
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
- Only final scores are kept from a feed. Per-quarter splits, box scores, and
  play-by-play are not used.

## Weekly routine

1. After Monday Night Football, screenshot the team-stat tables (about 12) into
   `inbox/season-2026/week-NN/`, one file per table, named by `table_key`.
2. In a Claude chat, ask for the week's schedule, final scores for finished games,
   spreads and totals, and the red zone pages. Save each CSV and
   sidecar into `extracted/season-2026/week-NN/`.
3. Screenshot sportsbook moneylines and four prop markets (receiving yards,
   receptions, rushing yards, anytime TD) for tracked players.
4. After the games, screenshot the weekly stat leader pages (receiving yards, rushing
   yards, receptions) and the TD scorers list to settle yardage props and anytime-TD
   picks.
5. Ask Claude Code to transcribe the screenshots and import everything. The Data
   Checklist tab shows what is still missing.

## Source terms

- The charted stats come from a paid analytics site whose terms prohibit automated
  access and copying without written consent. Treat it as private until there is a
  written yes for personal, non-commercial use.
- Public odds pages, red zone pages, and the sports feed are read one page at a time on
  request. Check each site's terms before making any of it routine.
- Sportsbook screenshots are for personal use.
- Nothing from a restricted source goes into a public deployment or README screenshots.
  The public demo runs on synthetic data.
