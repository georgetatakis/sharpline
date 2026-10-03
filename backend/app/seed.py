"""Seed the teams table with all 32 NFL teams.

Run with:  python -m app.seed
Safe to re-run: existing teams are updated in place, never duplicated.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team

# (name, nflverse abbreviation, conference, division)
TEAMS = [
    ("Buffalo Bills", "BUF", "AFC", "East"),
    ("Miami Dolphins", "MIA", "AFC", "East"),
    ("New England Patriots", "NE", "AFC", "East"),
    ("New York Jets", "NYJ", "AFC", "East"),
    ("Baltimore Ravens", "BAL", "AFC", "North"),
    ("Cincinnati Bengals", "CIN", "AFC", "North"),
    ("Cleveland Browns", "CLE", "AFC", "North"),
    ("Pittsburgh Steelers", "PIT", "AFC", "North"),
    ("Houston Texans", "HOU", "AFC", "South"),
    ("Indianapolis Colts", "IND", "AFC", "South"),
    ("Jacksonville Jaguars", "JAX", "AFC", "South"),
    ("Tennessee Titans", "TEN", "AFC", "South"),
    ("Denver Broncos", "DEN", "AFC", "West"),
    ("Kansas City Chiefs", "KC", "AFC", "West"),
    ("Las Vegas Raiders", "LV", "AFC", "West"),
    ("Los Angeles Chargers", "LAC", "AFC", "West"),
    ("Dallas Cowboys", "DAL", "NFC", "East"),
    ("New York Giants", "NYG", "NFC", "East"),
    ("Philadelphia Eagles", "PHI", "NFC", "East"),
    ("Washington Commanders", "WAS", "NFC", "East"),
    ("Chicago Bears", "CHI", "NFC", "North"),
    ("Detroit Lions", "DET", "NFC", "North"),
    ("Green Bay Packers", "GB", "NFC", "North"),
    ("Minnesota Vikings", "MIN", "NFC", "North"),
    ("Atlanta Falcons", "ATL", "NFC", "South"),
    ("Carolina Panthers", "CAR", "NFC", "South"),
    ("New Orleans Saints", "NO", "NFC", "South"),
    ("Tampa Bay Buccaneers", "TB", "NFC", "South"),
    ("Arizona Cardinals", "ARI", "NFC", "West"),
    ("Los Angeles Rams", "LA", "NFC", "West"),
    ("San Francisco 49ers", "SF", "NFC", "West"),
    ("Seattle Seahawks", "SEA", "NFC", "West"),
]


def seed_teams(session: Session) -> int:
    """Insert missing teams and update existing ones. Returns the number inserted."""
    existing = {t.abbreviation: t for t in session.scalars(select(Team))}
    inserted = 0
    for name, abbreviation, conference, division in TEAMS:
        team = existing.get(abbreviation)
        if team is None:
            session.add(
                Team(
                    name=name,
                    abbreviation=abbreviation,
                    conference=conference,
                    division=division,
                )
            )
            inserted += 1
        else:
            team.name, team.conference, team.division = name, conference, division
    session.commit()
    return inserted


if __name__ == "__main__":
    from app.db import SessionLocal

    with SessionLocal() as session:
        inserted = seed_teams(session)
    print(f"Seeded teams: {inserted} inserted, {len(TEAMS) - inserted} already present")
