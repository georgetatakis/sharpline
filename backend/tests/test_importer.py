import csv
import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.importer import import_csv, parse_value
from app.models import Base, DataSnapshot, Team, TeamDefenseStats, TeamOffenseStats
from app.seed import TEAMS, seed_teams

NAMES = [name for name, _, _, _ in TEAMS]


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_teams(session)
        yield session


def sidecar(**overrides):
    fields = {
        "source_name": "charting site",
        "source_url": "screenshot",
        "captured_at": "2026-10-14T18:45:00-04:00",
        "as_of_week": 5,
        "target_week": 6,
    }
    fields.update(overrides)
    return fields


def write_table(tmp_path, table_key, header, rows, meta=None, week=6):
    folder = tmp_path / "season-2026" / f"week-{week:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{table_key}.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    if meta is not False:
        (folder / f"{table_key}.meta.json").write_text(json.dumps(meta or sidecar(target_week=week, as_of_week=week - 1)))
    return path


OFFENSE_HEADER = ["team", "epa_per_play", "yards_per_play", "points_per_drive", "explosive_play_rate", "down_conversion_rate"]


def offense_rows(names=NAMES, epa="0.10"):
    return [[name, epa, "5.5", "2.1", "6.8%", "76.4%"] for name in names]


def write_offense(tmp_path, rows=None, **kwargs):
    return write_table(tmp_path, "team_offense_metrics", OFFENSE_HEADER, rows or offense_rows(), **kwargs)


def run(session, path, tmp_path):
    return import_csv(session, path, log_dir=tmp_path / "logs")


def offense_row(session, abbreviation, as_of_week=5):
    return session.scalar(
        select(TeamOffenseStats).join(Team).where(
            Team.abbreviation == abbreviation, TeamOffenseStats.as_of_week == as_of_week
        )
    )


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


# --- Parsing ---------------------------------------------------------------

@pytest.mark.parametrize(
    "text, kind, expected",
    [
        ("42.1%", "rate", 0.421),
        ("0.421", "rate", 0.421),
        ("+6.6", "pct_points", 0.066),
        ("−3.9", "pct_points", -0.039),  # unicode minus, as some pages render it
        ("0.21", "number", 0.21),
        ("32:12", "minutes", 32.2),
        ("32.2", "minutes", 32.2),
        ("", "rate", None),
        ("—", "rate", None),
    ],
)
def test_parse_value(text, kind, expected):
    value = parse_value(text, kind)
    if expected is None:
        assert value is None
    else:
        assert value == pytest.approx(expected)


@pytest.mark.parametrize("text, kind", [("42.1", "rate"), ("abc", "number"), ("5%", "number")])
def test_parse_value_refuses_ambiguous_or_garbled_text(text, kind):
    with pytest.raises(ValueError):
        parse_value(text, kind)


# --- Happy path ------------------------------------------------------------

def test_loads_full_table_and_records_snapshot(session, tmp_path):
    result = run(session, write_offense(tmp_path), tmp_path)

    assert result.status == "loaded"
    assert (result.rows_read, result.rows_loaded) == (32, 32)
    assert count(session, TeamOffenseStats) == 32
    kc = offense_row(session, "KC")
    assert kc.season == 2026
    assert kc.epa_per_play == pytest.approx(0.10)
    assert kc.explosive_play_rate == pytest.approx(0.068)
    snapshot = session.scalar(select(DataSnapshot))
    assert (snapshot.table_key, snapshot.status, snapshot.row_count) == ("team_offense_metrics", "loaded", 32)
    assert (snapshot.as_of_week, snapshot.target_week, snapshot.source_url) == (5, 6, "screenshot")


def test_team_names_in_any_form_map_to_teams(session, tmp_path):
    names = NAMES.copy()
    names[NAMES.index("Los Angeles Rams")] = "LAR"
    names[NAMES.index("San Francisco 49ers")] = "49ers"
    names[NAMES.index("Kansas City Chiefs")] = "Kansas City\nChiefs"

    result = run(session, write_offense(tmp_path, offense_rows(names)), tmp_path)

    assert result.status == "loaded"
    assert offense_row(session, "LA") is not None and offense_row(session, "SF") is not None


def test_import_writes_run_log(session, tmp_path):
    run(session, write_offense(tmp_path), tmp_path)

    end = json.loads((tmp_path / "logs" / "import.jsonl").read_text().splitlines()[-1])
    assert end["event"] == "run_end"
    assert end["table_key"] == "team_offense_metrics"
    assert (end["rows_read"], end["rows_loaded"], end["as_of_week"]) == (32, 32, 5)


# --- Whole-file rejections -------------------------------------------------

def test_csv_without_sidecar_is_refused_and_nothing_loads(session, tmp_path):
    result = run(session, write_offense(tmp_path, meta=False), tmp_path)

    assert result.status == "rejected" and "no sidecar" in result.reason
    assert count(session, TeamOffenseStats) == 0
    assert count(session, DataSnapshot) == 0


def test_sidecar_missing_a_field_is_refused(session, tmp_path):
    meta = sidecar()
    del meta["captured_at"]

    result = run(session, write_offense(tmp_path, meta=meta), tmp_path)

    assert result.status == "rejected" and "captured_at" in result.reason


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"as_of_week": 6}, "must be before target_week"),
        ({"target_week": 7, "as_of_week": 6}, "week-06"),
        ({"captured_at": "2026-10-14T18:45:00"}, "UTC offset"),
    ],
)
def test_bad_sidecar_is_refused_but_recorded(session, tmp_path, overrides, message):
    result = run(session, write_offense(tmp_path, meta=sidecar(**overrides)), tmp_path)

    assert result.status == "rejected" and message in result.reason
    assert count(session, TeamOffenseStats) == 0


def test_rejection_with_valid_sidecar_writes_rejected_snapshot(session, tmp_path):
    path = write_table(tmp_path, "team_offense_metrics", OFFENSE_HEADER + ["extra"], offense_rows())

    result = run(session, path, tmp_path)

    assert result.status == "rejected" and "unexpected extra" in result.reason
    snapshot = session.scalar(select(DataSnapshot))
    assert snapshot.status == "rejected" and "unexpected extra" in snapshot.notes


def test_missing_column_is_refused(session, tmp_path):
    header = OFFENSE_HEADER[:-1]
    path = write_table(tmp_path, "team_offense_metrics", header, [r[:-1] for r in offense_rows()])

    result = run(session, path, tmp_path)

    assert result.status == "rejected" and "missing down_conversion_rate" in result.reason


def test_file_outside_season_week_folders_is_refused(session, tmp_path):
    path = tmp_path / "team_offense_metrics.csv"
    path.write_text(",".join(OFFENSE_HEADER) + "\n")

    result = run(session, path, tmp_path)

    assert result.status == "rejected" and "season-YYYY/week-NN" in result.reason


def test_table_without_an_importer_yet_is_refused(session, tmp_path):
    path = write_table(tmp_path, "player_results", ["week", "player", "team", "position", "receiving_yards", "rushing_yards", "receptions"], [])

    result = run(session, path, tmp_path)

    assert result.status == "rejected" and "no importer" in result.reason


def test_file_with_no_usable_rows_is_refused(session, tmp_path):
    result = run(session, write_offense(tmp_path, [["Nowhere FC", "0.1", "5", "2", "5%", "70%"]]), tmp_path)

    assert result.status == "rejected" and "no usable rows" in result.reason


# --- Row-level problems ----------------------------------------------------

def test_unknown_team_row_is_rejected_and_rest_load(session, tmp_path):
    rows = offense_rows()
    rows[0][0] = "Nowhere FC"

    result = run(session, write_offense(tmp_path, rows), tmp_path)

    assert result.status == "partial"
    assert result.rows_loaded == 31
    assert any("unknown team 'Nowhere FC'" in r for r in result.rejected)
    assert any("missing teams (offense): Buffalo Bills" in f for f in result.flags)


def test_duplicate_team_rejects_every_copy(session, tmp_path):
    rows = offense_rows()
    rows[5] = ["Buffalo Bills", "0.30", "5.5", "2.1", "6.8%", "76.4%"]

    result = run(session, write_offense(tmp_path, rows), tmp_path)

    assert result.status == "partial"
    assert any("duplicate team Buffalo Bills" in r for r in result.rejected)
    assert offense_row(session, "BUF") is None
    assert result.rows_loaded == 30


def test_missing_teams_make_snapshot_partial(session, tmp_path):
    result = run(session, write_offense(tmp_path, offense_rows(NAMES[:31])), tmp_path)

    assert result.status == "partial"
    assert any("missing teams (offense): Seattle Seahawks" in f for f in result.flags)
    assert "Seattle Seahawks" in session.scalar(select(DataSnapshot.notes))


@pytest.mark.parametrize(
    "column, bad",
    [
        (1, "2.5"),   # EPA/play far outside any real season
        (1, "abc"),
        (5, "76.4"),  # a rate written without its % sign
    ],
)
def test_out_of_range_or_unparseable_value_rejects_the_row(session, tmp_path, column, bad):
    rows = offense_rows()
    rows[0][column] = bad

    result = run(session, write_offense(tmp_path, rows), tmp_path)

    assert result.status == "partial"
    assert len(result.rejected) == 1 and result.rejected[0].startswith("line 2")
    assert offense_row(session, "BUF") is None


def test_blank_cell_loads_as_null(session, tmp_path):
    rows = offense_rows()
    rows[0][2] = ""

    result = run(session, write_offense(tmp_path, rows), tmp_path)

    assert result.status == "loaded"
    assert offense_row(session, "BUF").yards_per_play is None


# --- Upsert behaviour ------------------------------------------------------

TENDENCIES_HEADER = ["team", "motion_rate", "play_action_rate", "air_yards_per_att", "shotgun_rate", "no_huddle_rate"]


def tendencies_rows(no_huddle="4.1%"):
    return [[name, "60.0%", "21.0%", "8.9", "44.7%", no_huddle] for name in NAMES]


def test_two_tables_fill_one_row_per_team(session, tmp_path):
    run(session, write_offense(tmp_path), tmp_path)
    run(session, write_table(tmp_path, "offensive_tendencies", TENDENCIES_HEADER, tendencies_rows()), tmp_path)

    assert count(session, TeamOffenseStats) == 32
    kc = offense_row(session, "KC")
    assert kc.epa_per_play == pytest.approx(0.10) and kc.motion_rate == pytest.approx(0.60)


def test_reimport_updates_in_place_without_duplicates(session, tmp_path):
    run(session, write_offense(tmp_path), tmp_path)
    run(session, write_offense(tmp_path), tmp_path)

    assert count(session, TeamOffenseStats) == 32
    assert count(session, DataSnapshot) == 2


def test_blank_cell_never_erases_an_earlier_value(session, tmp_path):
    run(session, write_offense(tmp_path), tmp_path)
    rows = offense_rows()
    rows[0][2] = ""
    run(session, write_offense(tmp_path, rows), tmp_path)

    assert offense_row(session, "BUF").yards_per_play == pytest.approx(5.5)


def test_new_as_of_week_adds_rows_and_keeps_history(session, tmp_path):
    run(session, write_offense(tmp_path), tmp_path)
    run(session, write_offense(tmp_path, offense_rows(epa="0.20"), week=7), tmp_path)

    assert count(session, TeamOffenseStats) == 64
    assert offense_row(session, "KC", as_of_week=5).epa_per_play == pytest.approx(0.10)
    assert offense_row(session, "KC", as_of_week=6).epa_per_play == pytest.approx(0.20)


# --- Stale and disagreeing sources ----------------------------------------

MATCHUP_HEADER = ["offense_team", "off_proe", "defense_team", "def_proe", "matchup_score"]


def import_season_then_matchup_proe(session, tmp_path, season_off_proe, matchup_off_proe):
    """Season PROE for all 32 teams, then this week's matchups; both fill off_proe."""
    season_rows = [[name, season_off_proe, "+1.0"] for name in NAMES]
    run(session, write_table(tmp_path, "proe_offense_defense", ["team", "off_proe", "def_proe"], season_rows), tmp_path)
    playing = NAMES[:28]
    matchup_rows = [
        [team, matchup_off_proe(team), playing[(i + 1) % 28], "+1.0", "+0.0"]
        for i, team in enumerate(playing)
    ]
    return run(session, write_table(tmp_path, "proe_matchups", MATCHUP_HEADER, matchup_rows), tmp_path)


def test_two_tables_disagreeing_on_a_shared_column_are_flagged(session, tmp_path):
    result = import_season_then_matchup_proe(
        session, tmp_path, "+3.0", lambda team: "+4.0" if team == "Kansas City Chiefs" else "+3.0"
    )

    disagreements = [f for f in result.flags if f.startswith("disagreement")]
    assert len(disagreements) == 1 and "Kansas City Chiefs off_proe" in disagreements[0]


def test_values_identical_to_previous_week_are_flagged_stale(session, tmp_path):
    run(session, write_offense(tmp_path), tmp_path)

    result = run(session, write_offense(tmp_path, week=7), tmp_path)

    assert any(f.startswith("stale") and "as_of_week 5" in f for f in result.flags)


def test_changed_values_are_not_flagged_stale(session, tmp_path):
    run(session, write_offense(tmp_path), tmp_path)

    result = run(session, write_offense(tmp_path, offense_rows(epa="0.20"), week=7), tmp_path)

    assert not any(f.startswith("stale") for f in result.flags)


# --- PROE matchups ---------------------------------------------------------

def test_proe_matchups_fill_offense_and_defense_and_allow_byes(session, tmp_path):
    playing = NAMES[:28]
    rows = [
        [playing[i], f"+{i / 10:.1f}", playing[(i + 1) % 28], f"-{i / 10:.1f}", "+1.0"]
        for i in range(28)
    ]
    header = ["offense_team", "off_proe", "defense_team", "def_proe", "matchup_score"]

    result = run(session, write_table(tmp_path, "proe_matchups", header, rows), tmp_path)

    assert result.status == "loaded"
    assert any(f.startswith("offense not in this table") and "Seattle Seahawks" in f for f in result.flags)
    mia = offense_row(session, "MIA")  # NAMES[1]
    assert mia.off_proe == pytest.approx(0.001)
    defense = session.scalar(
        select(TeamDefenseStats).join(Team).where(Team.abbreviation == "NE")  # NAMES[2]
    )
    assert defense.def_proe == pytest.approx(-0.001)


# --- Source conventions ----------------------------------------------------

DEFENSE_HEADER = ["team", "epa_per_play_allowed", "yards_per_play_allowed", "points_per_drive_allowed", "explosive_play_rate_allowed", "down_conversion_rate_allowed"]


def test_defense_epa_is_flipped_to_epa_allowed(session, tmp_path):
    # The charting site shows the best defense as positive (Vikings +0.22).
    rows = [[name, "0.22" if name == "Minnesota Vikings" else "-0.05", "5.0", "2.0", "5.0%", "70.0%"] for name in NAMES]

    run(session, write_table(tmp_path, "team_defense_metrics", DEFENSE_HEADER, rows), tmp_path)

    vikings = session.scalar(select(TeamDefenseStats).join(Team).where(Team.abbreviation == "MIN"))
    assert vikings.epa_per_play_allowed == pytest.approx(-0.22)


@pytest.mark.parametrize(
    "season, matchup, flagged",
    [
        ("+6.6", "+6.6", False),
        ("+6.6", "+7", False),  # whole-number display of the same value
        ("+6.6", "+6", True),   # 6.6 would display as 7, so the sources really differ
    ],
)
def test_disagreement_allows_for_display_rounding(session, tmp_path, season, matchup, flagged):
    result = import_season_then_matchup_proe(session, tmp_path, season, lambda team: matchup)

    assert any(f.startswith("disagreement") for f in result.flags) is flagged


TEMPO_HEADER = ["team", "play_clock_used", "neutral_play_clock", "neutral_pass_rate", "gear_change", "no_huddle_rate_tempo"]


def test_tempo_no_huddle_never_overwrites_tendencies_no_huddle(session, tmp_path):
    # Tempo leaves out snaps after timeouts, penalties and the like, so it's a different metric.
    run(session, write_table(tmp_path, "offensive_tendencies", TENDENCIES_HEADER, tendencies_rows("8.0%")), tmp_path)
    tempo_rows = [[name, "31.0", "31.0", "55.0%", "—", "7%"] for name in NAMES]

    result = run(session, write_table(tmp_path, "offensive_pace_tempo", TEMPO_HEADER, tempo_rows), tmp_path)

    assert result.status == "loaded" and not result.flags
    kc = offense_row(session, "KC")
    assert kc.no_huddle_rate == pytest.approx(0.08)
    assert kc.neutral_play_clock == pytest.approx(31.0) and kc.gear_change is None
