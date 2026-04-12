from flask import Flask, render_template, request, jsonify
import sqlite3
import re
import os
import io
import csv
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ── Active table tracker ──────────────────────────────────────────
_active_csv_table = None

# ── LLM ───────────────────────────────────────────────────────────
def llm_available():
    return bool(os.environ.get("GROQ_API_KEY", ""))

def try_llm_sql(question, tables, active_table):
    try:
        from llm_sql import generate_sql_llm
        return generate_sql_llm(question, tables, active_table)
    except Exception as e:
        print(f"⚠️  LLM failed: {e}")
        return None

# ── Database ──────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect("store.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    # Just ensure the DB file exists — no hardcoded tables
    conn = get_conn()
    conn.close()

# ── Schema helpers ────────────────────────────────────────────────
def get_tables():
    """Returns only the active uploaded table, or all if none set."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = [r[0] for r in cur.fetchall()]
    tables = {}
    # Only show the active CSV table in UI
    show = [_active_csv_table] if _active_csv_table and _active_csv_table in all_tables else all_tables
    for t in show:
        cur.execute(f"PRAGMA table_info([{t}])")
        tables[t] = [row[1] for row in cur.fetchall()]
    conn.close()
    return tables

def find_col(cols, *hints):
    for hint in hints:
        for c in cols:
            if hint.lower() in c.lower():
                return c
    return cols[0] if cols else None

def get_schema():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = [r[0] for r in cur.fetchall()]
    tables = [_active_csv_table] if _active_csv_table and _active_csv_table in all_tables else all_tables
    nodes, edges = [], []
    colors = ["#6366f1","#f59e0b","#10b981","#ef4444","#8b5cf6","#06b6d4"]
    for i, t in enumerate(tables):
        cur.execute(f"PRAGMA table_info({t})")
        fields = [c[1] + (" (PK)" if c[5] else "") for c in cur.fetchall()]
        nodes.append({"id": t, "label": t, "color": colors[i % len(colors)], "fields": fields})
        cur.execute(f"PRAGMA foreign_key_list({t})")
        for fk in cur.fetchall():
            edges.append({"from": fk[2], "to": t, "label": f"{fk[4]} → {fk[3]}"})
    conn.close()
    return {"nodes": nodes, "edges": edges}

# ── SQL helpers ───────────────────────────────────────────────────
def is_valid_sql(sql):
    if not sql or len(sql) < 6: return False
    if not sql.strip().upper().startswith("SELECT"): return False
    if "FROM" not in sql.upper(): return False
    if sql.upper().count("GROUP BY") > 2 or sql.upper().count("ORDER BY") > 2: return False
    # Catch bare aggregate keywords used as column names (LLM hallucination)
    import re as _re
    if _re.search(r'\bSELECT\s+(MAX|MIN|SUM|AVG|COUNT)\s+FROM\b', sql, _re.IGNORECASE):
        return False
    return True

def fix_sql(sql):
    sql = sql.strip().rstrip(';')
    sql = re.sub(r'\s+', ' ', sql)
    if re.match(r'^ELECT\b', sql, re.IGNORECASE):
        sql = 'S' + sql
    return sql + ';'

def is_safe(sql):
    return not any(w in sql.upper() for w in
                   ["DELETE","DROP","UPDATE","INSERT","ALTER","TRUNCATE","CREATE"])

def exec_sql(sql):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

# ── SQL Generation ────────────────────────────────────────────────
def generate_sql(question, active_table=None):
    q = question.lower().strip()
    tables = get_tables()

    # Raw SQL passthrough
    if q.startswith("select"):
        return fix_sql(question)

    target = active_table if (active_table and active_table in tables) \
             else (list(tables.keys())[0] if tables else None)
    if not target:
        raise ValueError("No table found. Upload a CSV or use the default data.")

    # LLM first
    if llm_available():
        sql = try_llm_sql(question, tables, target)
        if sql and is_valid_sql(sql):
            return fix_sql(sql)

    # Rule-based fallback
    cols = tables[target]
    num_col  = find_col(cols, 'amount','price','salary','revenue','sales','total',
                        'cost','value','score','qty','quantity','marks','grade','age','enrollment')
    name_col = find_col(cols, 'name','title','label','product','item','employee',
                        'customer','user','student','person','sponsor')
    date_col = find_col(cols, 'date','time','created','updated','timestamp','year','month')
    t = target

    rules = [
        (r"how many|count all|total count|number of", f"SELECT COUNT(*) as count FROM [{t}];"),
        (r"show all|list all|all records|all data|display all|get all|fetch all",
            f"SELECT * FROM [{t}];"),
    ]
    if num_col:
        rules += [
            (r"total|sum\b",      f"SELECT SUM(CAST([{num_col}] AS REAL)) as total FROM [{t}];"),
            (r"average|avg\b|mean\b", f"SELECT AVG(CAST([{num_col}] AS REAL)) as average FROM [{t}];"),
            (r"highest|maximum|max\b|largest",
                f"SELECT * FROM [{t}] ORDER BY CAST([{num_col}] AS REAL) DESC LIMIT 1;"),
            (r"lowest|minimum|min\b|smallest",
                f"SELECT * FROM [{t}] ORDER BY CAST([{num_col}] AS REAL) ASC LIMIT 1;"),
            (r"above (\d+(?:\.\d+)?)|greater than (\d+(?:\.\d+)?)|more than (\d+(?:\.\d+)?)",
                lambda m, _t=t, _c=num_col:
                    f"SELECT * FROM [{_t}] WHERE CAST([{_c}] AS REAL) > {m.group(1) or m.group(2) or m.group(3)};"),
            (r"below (\d+(?:\.\d+)?)|less than (\d+(?:\.\d+)?)|under (\d+(?:\.\d+)?)",
                lambda m, _t=t, _c=num_col:
                    f"SELECT * FROM [{_t}] WHERE CAST([{_c}] AS REAL) < {m.group(1) or m.group(2) or m.group(3)};"),
            (r"sort|order by|highest first",
                f"SELECT * FROM [{t}] ORDER BY CAST([{num_col}] AS REAL) DESC;"),
            (r"lowest first|ascending",
                f"SELECT * FROM [{t}] ORDER BY CAST([{num_col}] AS REAL) ASC;"),
        ]
    if name_col and num_col:
        rules += [
            (r"top\s*(\d+)",
                lambda m, _t=t, _n=name_col, _c=num_col:
                    f"SELECT [{_n}], COUNT(*) as count FROM [{_t}] GROUP BY [{_n}] ORDER BY count DESC LIMIT {m.group(1)};"),
            (r"bottom\s*(\d+)",
                lambda m, _t=t, _n=name_col, _c=num_col:
                    f"SELECT [{_n}], CAST([{_c}] AS REAL) as value FROM [{_t}] ORDER BY CAST([{_c}] AS REAL) ASC LIMIT {m.group(1)};"),
            (r"top\b|best\b",
                f"SELECT [{name_col}], COUNT(*) as count FROM [{t}] GROUP BY [{name_col}] ORDER BY count DESC LIMIT 5;"),
            (r"group|per |each ",
                f"SELECT [{name_col}], COUNT(*) as count FROM [{t}] GROUP BY [{name_col}] ORDER BY count DESC;"),
        ]
    if date_col:
        rules += [
            (r"recent|latest|newest",  f"SELECT * FROM [{t}] ORDER BY [{date_col}] DESC LIMIT 10;"),
            (r"oldest|earliest",       f"SELECT * FROM [{t}] ORDER BY [{date_col}] ASC LIMIT 10;"),
            (r"2024", f"SELECT * FROM [{t}] WHERE [{date_col}] LIKE '2024%';"),
            (r"2023", f"SELECT * FROM [{t}] WHERE [{date_col}] LIKE '2023%';"),
            (r"monthly",
                f"SELECT strftime('%Y-%m', [{date_col}]) as month, COUNT(*) as count "
                f"FROM [{t}] GROUP BY month ORDER BY month;"),
        ]
    if name_col:
        rules += [
            (r'find\s+([^\s]+)|search\s+([^\s]+)',
                lambda m, _t=t, _n=name_col:
                    f"SELECT * FROM [{_t}] WHERE [{_n}] LIKE '%{(m.group(1) or m.group(2)).strip()}%';"),
            (r"unique|distinct", f"SELECT DISTINCT [{name_col}] FROM [{t}];"),
        ]
    rules += [
        (r"first\s*(\d+)|limit\s*(\d+)",
            lambda m, _t=t: f"SELECT * FROM [{_t}] LIMIT {m.group(1) or m.group(2)};"),
        (r".*", f"SELECT * FROM [{t}] LIMIT 50;"),
    ]

    for pattern, sql in rules:
        m = re.search(pattern, q)
        if m:
            return sql(m) if callable(sql) else sql

    return f"SELECT * FROM [{t}] LIMIT 50;"

# ── Explanation ───────────────────────────────────────────────────
def explain_sql(sql, question):
    # Use LLM for a proper explanation if available
    if llm_available():
        try:
            from llm_sql import get_client
            response = get_client().chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": 
                        "You are a SQL teacher. Given a SQL query and the original question, "
                        "explain what the query does in simple plain English. "
                        "Return a JSON object with exactly two keys: "
                        "'summary' (one sentence what the query returns) and "
                        "'steps' (array of strings, one per SQL clause explaining what it does). "
                        "No markdown, just raw JSON."},
                    {"role": "user", "content": f"Question: {question}\nSQL: {sql}"}
                ],
                temperature=0,
                max_tokens=400,
            )
            import json as _json
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"```json|```", "", raw).strip()
            data = _json.loads(raw)
            if "summary" in data and "steps" in data:
                return data
        except Exception:
            pass

    # Fallback: regex-based
    u = sql.upper()
    steps, summary = [], f"Retrieves data based on: '{question}'"
    if "SELECT" in u:
        m = re.search(r"SELECT\s+(.+?)\s+FROM", u)
        col = m.group(1) if m else "*"
        steps.append(f"SELECT {col} — fetches {'all columns' if col.strip()=='*' else 'specific columns'}")
    if "JOIN" in u:   steps.append("JOIN — combines rows from multiple tables")
    if "WHERE" in u:
        m = re.search(r"WHERE\s+(.+?)(?:\s+GROUP|\s+ORDER|\s+LIMIT|$)", u)
        if m: steps.append(f"WHERE {m.group(1).strip()} — filters rows")
    if "GROUP BY" in u: steps.append("GROUP BY — groups rows for aggregates")
    if "SUM(" in u:   summary = "Calculates total sum";   steps.append("SUM() — adds up values")
    if "AVG(" in u:   summary = "Calculates average";     steps.append("AVG() — computes average")
    if "COUNT(" in u: steps.append("COUNT() — counts rows")
    if "MAX(" in u:   steps.append("MAX() — finds highest value")
    if "MIN(" in u:   steps.append("MIN() — finds lowest value")
    if "HAVING" in u:
        m = re.search(r"HAVING\s+(.+?)(?:\s+ORDER|\s+LIMIT|$)", u)
        if m: steps.append(f"HAVING {m.group(1).strip()} — filters after grouping")
    if "ORDER BY" in u:
        steps.append(f"ORDER BY — sorts {'descending' if 'DESC' in u else 'ascending'}")
    if "LIMIT" in u:
        m = re.search(r"LIMIT\s+(\d+)", u)
        steps.append(f"LIMIT {m.group(1) if m else 'n'} — returns top rows only")
    if not steps: steps.append("Fetches matching data from the database")
    return {"summary": summary, "steps": steps}

# ── Routes ────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/status")
def status():
    return jsonify({"model_ready": True, "llm_active": llm_available()})

@app.route("/query", methods=["POST"])
def query():
    data = request.json or {}
    question = (data.get("question") or "").strip()
    active_table = (data.get("active_table") or "").strip() or None
    if not question:
        return jsonify({"error": "Please enter a question"}), 400
    if len(question) > 300:
        return jsonify({"error": "Question too long (max 300 chars)"}), 400
    sql = None
    try:
        sql = generate_sql(question, active_table)
        if not is_safe(sql):
            return jsonify({"error": "Only SELECT queries are allowed"}), 403
        results = exec_sql(sql)
        return jsonify({
            "sql": sql,
            "results": results,
            "explanation": explain_sql(sql, question),
            "schema": get_schema(),
            "count": len(results)
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except sqlite3.Error as e:
        return jsonify({"error": f"SQL error: {e}", "sql": sql or ""}), 422
    except Exception as e:
        return jsonify({"error": f"Error: {e}"}), 500

@app.route("/upload-csv", methods=["POST"])
def upload_csv():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    if not f.filename.endswith(".csv"):
        return jsonify({"error": "Only CSV files are supported"}), 400
    try:
        global _active_csv_table
        stream = io.StringIO(f.stream.read().decode("utf-8"), newline=None)
        rows = list(csv.DictReader(stream))
        if not rows:
            return jsonify({"error": "CSV file is empty"}), 400
        table_name = re.sub(r"[^a-z0-9_]", "_", f.filename.replace(".csv","").lower())
        cols = list(rows[0].keys())
        conn = sqlite3.connect("store.db")
        cur = conn.cursor()

        # Drop ALL existing tables before loading new CSV
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        for (t,) in cur.fetchall():
            cur.execute(f"DROP TABLE IF EXISTS [{t}]")

        cur.execute(f"CREATE TABLE [{table_name}] ({', '.join(f'[{c}] TEXT' for c in cols)})")
        placeholders = ", ".join("?" * len(cols))
        for row in rows:
            cur.execute(f"INSERT INTO [{table_name}] VALUES ({placeholders})",
                        [row.get(c, "") for c in cols])
        conn.commit()
        conn.close()
        _active_csv_table = table_name
        return jsonify({"message": f"Table '{table_name}' created with {len(rows)} rows",
                        "table": table_name, "columns": cols, "preview": rows[:5]})
    except Exception as e:
        return jsonify({"error": f"Failed to process CSV: {e}"}), 500

@app.route("/run-sql", methods=["POST"])
def run_sql():
    data = request.json or {}
    sql = (data.get("sql") or "").strip()
    if not sql:
        return jsonify({"error": "No SQL provided"}), 400
    if not is_safe(sql):
        return jsonify({"error": "Only SELECT queries are allowed"}), 403
    try:
        results = exec_sql(sql)
        return jsonify({"results": results, "count": len(results)})
    except sqlite3.Error as e:
        return jsonify({"error": f"SQL error: {e}"}), 422

@app.route("/schema")
def schema_route():
    return jsonify(get_schema())

@app.route("/tables")
def tables_route():
    return jsonify(get_tables())

if __name__ == "__main__":
    init_db()
    print("🗄️  Database ready")
    print(f"🤖 LLM: {'✅ Groq (Llama 3.3 70B)' if llm_available() else '⚠️  No GROQ_API_KEY — rule-based fallback active'}")
    app.run(debug=True)
