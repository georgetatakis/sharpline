# Sharpline

A predictive analytics platform for NFL games. It covers game outcomes, player
props and anytime-TD probability, and compares model output against the
betting market.

## Repo layout

| Directory   | Contents                                                         |
|-------------|------------------------------------------------------------------|
| `backend/`  | FastAPI app, SQLAlchemy models, Alembic migrations, pytest tests |
| `frontend/` | React (Vite) + Tailwind + Recharts UI                            |
| `data/`     | Ingestion scripts (`data/ingestion/`) and model modules (`data/models/`), run as scheduled scripts rather than called live by the API |

## Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy, Alembic
- **Database:** PostgreSQL
- **Data / modeling:** pandas, nfl_data_py, scikit-learn, XGBoost
- **Odds:** The Odds API
- **Frontend:** React, Tailwind CSS, Recharts
- **Local dev:** Docker Compose

## Status

Early scaffold. Setup instructions and model hit-rate results will be added as
the project is built out.
