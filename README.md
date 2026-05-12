# StrataCore — Analytics & Decision Support System

StrataCore is a **natural language → SQL** assistant for **CSV and Excel** datasets. It uses **FastAPI**, loads data with **Pandas**, runs queries on **SQLite**, and renders charts with **Plotly.js**. Optional **Groq (Llama 3.3)** powers ReAct-style SQL generation, validation, self-correction on errors, intent classification, AI summaries, and conversational follow-ups.

---

## Tech stack

| Layer | Technology |
|-------|------------|
| API | Python, **FastAPI** |
| Data | **Pandas**, **SQLite** (tabular), **SQL** |
| LLM | Groq — Llama 3.3 70B |
| UI | Jinja2 templates, **Plotly.js** |

---

## Quick start

```bash
pip install -r requirements.txt
```

Create `.env` (optional but recommended for full NL→SQL):

```
GROQ_API_KEY=your_key_here
```

Run the server:

```bash
python -m stratacore
```

Or:

```bash
uvicorn stratacore.main:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**

---

## Features

- **NL→SQL** with ReAct prompting, sample-row RAG, and **SELECT-only** validation  
- **SQL error recovery**: failed queries are sent back to the model (retry loop)  
- **Intent classification**: aggregation, trend, comparison, filter, lookup  
- **Plotly** charts (bar, line, pie) plus single-metric stat view  
- **AI insights** and suggested follow-up questions  
- **Conversation memory** (recent turns) for follow-up questions  

---

## API (selected)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Web UI |
| GET | `/status` | LLM availability |
| POST | `/query` | NL → SQL → results + insights + `chart_type` + `intent` |
| POST | `/upload-table` | Multipart file: `.csv`, `.xlsx`, `.xls` |
| POST | `/chart-figure` | JSON body: results + optional `chart_type` → Plotly payload |
| POST | `/run-sql` | Run validated SELECT |
| POST | `/clear-db`, `/clear-history` | Reset data or chat context |
| GET | `/tables`, `/schema`, `/metrics` | Schema and simple metrics |

---

## Project layout

```
stratacore/
  __init__.py
  __main__.py       # uvicorn entry
  main.py           # FastAPI routes
  config.py
  core_state.py     # session state (active table, history, metrics)
  db.py             # SQLite helpers
  sql_ops.py        # validation and execution
  ingestion.py      # Pandas → SQLite
  plotly_figures.py # chart JSON for Plotly.js
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
