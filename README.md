# QueryMind — AI SQL Assistant

A web app that converts plain English questions into SQL queries using Llama 3.3 70B. Upload any CSV, ask questions naturally, get instant SQL + results + explanation.

---

## How It Works

1. Upload a CSV file → stored as a table in SQLite
2. Type a question in plain English
3. Question + table schema sent to **Llama 3.3 70B** via Groq API
4. LLM generates correct SQL knowing your exact column names
5. SQL runs against SQLite → results + explanation shown

---

## Stack

| Layer | Technology |
|---|---|
| Web server | Flask (Python) |
| Database | SQLite |
| LLM | Llama 3.3 70B via Groq API (free) |
| Frontend | HTML / CSS / JS |

---

## Project Structure

```
├── app.py              # Flask server, routes, SQL execution
├── llm_sql.py          # Groq API + Llama 3.3 SQL generation
├── templates/
│   └── index.html      # Frontend UI
├── requirements.txt    # Dependencies
├── .env                # GROQ_API_KEY (not committed)
└── store.db            # Auto-created SQLite database
```

---

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Get a free Groq API key
Sign up at https://console.groq.com (free, no credit card)

### 3. Add your key to `.env`
```
GROQ_API_KEY=your_key_here
```

### 4. Run the app
```bash
python app.py
```

### 5. Open browser
```
http://localhost:5000
```

---

## Features

- Upload any CSV — becomes a queryable table instantly
- Natural language to SQL via Llama 3.3 70B
- LLM-generated step-by-step SQL explanation
- Live table + column viewer
- Safety filter blocking DELETE / DROP / UPDATE
- Rule-based fallback if LLM is unavailable
- Raw SQL passthrough (type SELECT directly)

---

## Example Queries

| Question | What it does |
|---|---|
| `show all` | Returns all rows |
| `how many` | COUNT(*) |
| `top 10 sponsors` | GROUP BY + ORDER BY + LIMIT 10 |
| `average enrollment` | AVG() on numeric column |
| `trials started in 2024` | WHERE date LIKE '2024%' |
| `find NIH` | WHERE sponsor LIKE '%NIH%' |
| `SELECT * FROM [table] LIMIT 5` | Raw SQL passthrough |

---

## Viva Q&A

**Q: What LLM are you using?**
> Llama 3.3 70B served via Groq API. The table schema is injected into the system prompt so the model knows exact column names before generating SQL.

**Q: How does schema injection work?**
> Before sending the question to the LLM, we read all table names and column names from SQLite and include them in the system prompt. This gives the model full context to generate accurate SQL for any uploaded dataset.

**Q: How do you prevent harmful queries?**
> A safety filter blocks any SQL containing DELETE, DROP, UPDATE, INSERT, ALTER, TRUNCATE, or CREATE before execution.

**Q: What happens if the LLM fails?**
> A rule-based fallback handles common patterns like total, average, top N, filter by value, sort, and date filtering — so the app works even without an API key.
