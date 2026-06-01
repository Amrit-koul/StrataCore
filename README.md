# StrataCore — Analytics & Decision Support System

StrataCore is a **natural language → SQL** assistant for **CSV and Excel** datasets. It uses **FastAPI**, loads data with **Pandas**, runs queries on **SQLite**, and renders charts with **Plotly.js**. Optional **Groq (Llama 3.3)** powers ReAct-style SQL generation, validation, self-correction on errors, intent classification, AI summaries, and conversational follow-ups.

---

## Business problem solved

Many teams have “data in files” (CSV/Excel) but no fast way for non-SQL users to answer questions without exporting to a BI tool or asking an analyst.

StrataCore shortens time-to-answer by letting a user:

1. Upload a spreadsheet
2. Ask a question in natural language
3. Get transparent, executable SQL + results + a chart

This is a good fit for quick analysis, demos, and lightweight “spreadsheet analytics” workflows.

---
### Request/data flow (question → answer)

1. UI sends a question to `POST /query`.
2. Server generates SQL via LLM (schema + sample rows) or falls back to rules.
3. SQL is validated as SELECT-only, then executed on SQLite.
4. Results are returned with an explanation and optional insights/follow-ups.
5. UI requests `/chart-figure` to render Plotly charts.

---

## Tech stack

| Layer | Technology |
|-------|------------|
| API | Python, **FastAPI** |
| Data | **Pandas**, **SQLite** (tabular), **SQL** |
| LLM | Groq — Llama 3.3 70B (optional) |
| UI | Jinja2 templates, **Plotly.js** |

---

## Quick start

Install deps:

```bash
pip install -r requirements.txt
```

Optional (recommended): use a virtual environment.

```bash
python -m venv .venv
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Enable LLM features (optional): create a `.env` file in the project root:

```
GROQ_API_KEY=your_key_here
```

Run the server:

```bash
python -m stratacore
```

Or (explicitly via Uvicorn):

```bash
python -m uvicorn stratacore.main:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**

---

## Features

- **NL→SQL** with ReAct prompting, sample-row context, and **SELECT-only** validation
- **SQL error recovery**: retry/self-correction loop on SQLite errors
- **Intent classification**: aggregation, trend, comparison, filter, lookup
- **Plotly** charts (bar, line, pie) plus single-metric stat view
- Optional **AI insights** and **suggested follow-ups**
- Lightweight conversation memory for follow-up questions

---

## Tradeoffs and design decisions

- **SQLite (embedded) vs. Postgres**: minimal setup and great for demos; not designed for high concurrency or multi-tenant use.
- **All columns stored as TEXT**: ingestion is robust for messy spreadsheets; numeric work requires casts (the LLM is prompted to use `CAST(... AS REAL)`).
- **String-based safety checks**: SELECT-only blocking is simple; an AST-based SQL parser would be more robust.
- **Process-local state**: history/metrics are in-memory; production would use per-user sessions + persistence.
- **LLM reliability vs. UX**: validation + retries improve success rate at the cost of extra latency.

### Current limitations

- Single dataset at a time (upload clears existing tables)
- No authentication/authorization
- Demo-grade guardrails

---

## API (selected)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Web UI |
| GET | `/status` | LLM availability |
| POST | `/query` | NL → SQL → results + optional insights + `chart_type` + `intent` |
| POST | `/upload-table` | Multipart file: `.csv`, `.xlsx`, `.xls` |
| POST | `/chart-figure` | JSON: results + optional `chart_type` → Plotly payload |
| POST | `/run-sql` | Run validated SELECT |
| POST | `/clear-db`, `/clear-history` | Reset data or chat context |
| GET | `/tables`, `/schema`, `/metrics` | Schema + simple metrics |

---

## Project layout

```
stratacore/
  __init__.py
  __main__.py       # uvicorn entry
  main.py           # FastAPI routes
  config.py
  core_state.py     # state (active table, history, metrics)
  db.py             # SQLite helpers
  sql_ops.py        # validation and execution
  ingestion.py      # Pandas → SQLite
  plotly_figures.py # Plotly payload builder
  query_engine.py   # NL→SQL orchestration + rule fallback
  llm_sql.py        # Groq prompts (ReAct, insights, follow-ups)
templates/
  index.html
requirements.txt
pyproject.toml
```

Database file: `store.db` (gitignored). Override path with env `STRATACORE_DB` if needed.

---

## Requirements

Python 3.10+. Dependencies are listed in `requirements.txt` / `pyproject.toml`.
