"""Map the many ways a source names a team onto teams.id.

Sources write "Los Angeles Rams", "Rams", "LAR" or "LA" for the same team, and wrapped
table cells can split a name across lines. Matching ignores case and extra whitespace.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team

# Abbreviations other feeds use, mapped to the one stored in teams.abbreviation.
ABBREVIATION_ALIASES = {
    "LAR": "LA",
    "JAC": "JAX",
    "WSH": "WAS",
    "LVR": "LV",
    "KCC": "KC",
    "NOS": "NO",
    "GBP": "GB",
    "TBB": "TB",
    "SFO": "SF",
    "NEP": "NE",
    # Older nflverse seasons use the franchise's previous city.
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LA",
}


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


class TeamLookup:
    def __init__(self, session: Session):
        teams = session.scalars(select(Team)).all()
        by_abbreviation = {t.abbreviation: t.id for t in teams}
        self._ids: dict[str, int] = {}
        for team in teams:
            self._ids[_normalize(team.name)] = team.id
            self._ids[_normalize(team.abbreviation)] = team.id
            # The nickname is the last word of every current team name ("49ers", "Commanders").
            self._ids[_normalize(team.name.split()[-1])] = team.id
        for alias, abbreviation in ABBREVIATION_ALIASES.items():
            if abbreviation in by_abbreviation:
                self._ids[_normalize(alias)] = by_abbreviation[abbreviation]

    def team_id(self, text: str) -> int | None:
        """The team's id, or None if the text matches no team."""
        return self._ids.get(_normalize(text))
