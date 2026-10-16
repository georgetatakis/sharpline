# Sharpline

A predictive analytics platform for NFL games. It covers game outcomes, player
props and anytime-TD probability, and compares model output against the
betting market.

## Repo layout

| Directory   | Contents                                                         |
|-------------|------------------------------------------------------------------|
| `backend/`  | FastAPI app, SQLAlchemy models, Alembic migrations, pytest tests |
| `frontend/` | React (Vite) + Tailwind + Recharts UI                            |
| `data/`     | CSV and sidecar templates, and the data README. Screenshots (`data/inbox/`) and extracted CSVs (`data/extracted/`) stay local. See [data/README.md](data/README.md) |

## Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy, Alembic
- **Database:** PostgreSQL
- **Data / modeling:** pandas, scikit-learn, XGBoost
- **Data entry:** screenshots and chat-pulled CSVs, loaded by one import script
- **Frontend:** React, Tailwind CSS, Recharts
- **Local dev:** Docker Compose

## Status

Early scaffold. Setup instructions and model hit-rate results will be added as
the project is built out.
