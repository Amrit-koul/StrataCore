# QueryMind — Conversational AI Data Analyst

A full-stack AI system that lets you talk to any CSV dataset in plain English. Powered by **Llama 3.3 70B** via Groq, it generates SQL using ReAct reasoning, corrects its own mistakes, visualizes results, and explains everything step by step.

---

## What It Does

Upload any CSV → ask questions in plain English → get instant SQL + results + chart + AI insights + follow-up suggestions. No SQL knowledge needed.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask |
| Database | SQLite |
| LLM | Llama 3.3 70B via Groq API (free) |
| Frontend | HTML, CSS, Vanilla JS |
| Charts | Chart.js |

---

## Project Structure

```
├── app.py                  # Flask server — routes, SQL execution, RAG, metrics
├── llm_sql.py              # LLM engine — ReAct, RAG, self-correction, insights
├── templates/
│   └── index.html          # Full frontend UI
├── requirements.txt        # Python dependencies
├── .env                    # GROQ_API_KEY (not committed)
├── .gitignore
└── store.db                # Auto-created SQLite DB (not committed)
```

---

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Get a free Groq API key
Sign up at https://console.groq.com — free, no credit card required.

### 3. Add your key to `.env`
```
GROQ_API_KEY=your_key_here
```

### 4. Run
```bash
python app.py
```

### 5. Open browser
```
http://localhost:5000
```

---

## Advanced LLM Features

### ReAct Reasoning
Instead of directly converting a question to SQL, the LLM reasons step by step:
```
Thought: What is the user asking for?
Plan:    Which columns, aggregations, filters are needed?
SQL:     SELECT [sponsor], COUNT(*) AS count FROM [trials] GROUP BY [sponsor];
```
This makes queries significantly more accurate on complex questions.

### RAG — Retrieval-Augmented Generation
Before sending to the LLM, 5 real rows from your table are fetched and injected into the prompt. The LLM sees actual data values (not just column names), so it understands formats like `IsHoliday = True/False` or `enrollment = 1200`.

### Self-Correcting Loop
If the generated SQL throws a SQLite error, the error message is fed back to the LLM with the broken query and it attempts to fix it — up to 2 retries automatically.

### Intent Classification
Every question is classified before hitting the LLM:
- `aggregation` → COUNT, SUM, AVG queries
- `trend` → time-series, line charts
- `comparison` → side-by-side analysis
- `filter` → WHERE clause queries
- `lookup` → SELECT with LIMIT

### Conversational Memory
The last 3 exchanges are sent as context with every new question. You can ask follow-ups like:
- "now filter that by holiday only"
- "same but for store 5"
- "show me the top 10 instead"

### AI Insights
After every query, the LLM generates 2–3 specific bullet-point insights about what the data reveals, mentioning actual values from the results.

### Smart Follow-up Suggestions
The LLM suggests 3 relevant next questions based on your current query and schema. Click any chip to instantly run it.

### Smart Chart Type
Chart type is chosen by the LLM based on intent and result shape:
- Trend / date columns → Line chart
- Small categories (≤6) → Pie chart
- Comparisons / rankings → Bar chart
- Single number → Stat card

---

## API Routes

| Method | Route | Description |
|---|---|---|
| GET | `/` | Serve frontend |
| GET | `/status` | LLM availability check |
| POST | `/query` | Main NL→SQL→results endpoint |
| POST | `/upload-csv` | Upload CSV, create SQLite table |
| POST | `/clear-db` | Drop all tables, reset session |
| POST | `/clear-history` | Clear conversation memory |
| POST | `/chart-data` | Generate chart config from results |
| POST | `/run-sql` | Execute raw SQL directly |
| GET | `/schema` | Return DB schema as graph nodes/edges |
| GET | `/tables` | Return all tables and columns |
| GET | `/metrics` | Query stats — success rate, latency, self-corrections |

---

## `/query` Response Shape

```json
{
  "sql": "SELECT [sponsor], COUNT(*) AS count FROM [trials] GROUP BY [sponsor];",
  "results": [{"sponsor": "NIH", "count": 42}, ...],
  "explanation": {
    "summary": "Groups trials by sponsor and counts each one.",
    "steps": ["SELECT — fetches sponsor and count", "GROUP BY — groups by sponsor", "..."]
  },
  "insights": [
    "NIH leads with 42 trials, nearly 3x the next sponsor.",
    "Top 5 sponsors account for 60% of all trials."
  ],
  "followups": [
    "Average enrollment per sponsor?",
    "Which sponsor has most completed trials?",
    "Top sponsors by phase 3 trials?"
  ],
  "chart_type": "bar",
  "count": 25,
  "latency_ms": 1840
}
```

---

## Safety

All SQL is validated before execution:
- Only `SELECT` queries allowed
- Blocks: `DELETE`, `DROP`, `UPDATE`, `INSERT`, `ALTER`, `TRUNCATE`, `CREATE`, `EXEC`, `PRAGMA`
- LLM output validated for bracket-wrapped column names before execution
- Self-correction loop catches runtime SQL errors

---

## Example Queries

| Question | What it does |
|---|---|
| `top 10 sponsors by enrollment` | GROUP BY + ORDER BY + LIMIT |
| `average duration by phase` | AVG with GROUP BY |
| `trials started in 2020` | WHERE date LIKE filter |
| `count by status` | COUNT with GROUP BY |
| `how many trials` | COUNT(*) stat card |
| `trials with enrollment above 1000` | WHERE with CAST numeric filter |
| `monthly trend` | strftime GROUP BY month |
| `SELECT * FROM [trials] LIMIT 5` | Raw SQL passthrough |

---

## Requirements

```
flask==3.0.3
groq>=0.9.0
python-dotenv>=1.0.0
```
