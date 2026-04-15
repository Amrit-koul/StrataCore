from flask import Flask, render_template, request, jsonify
import sqlite3
import re
import os
import io
import csv
import time
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

import logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

# ── State ─────────────────────────────────────────────────────────
_active_csv_table = None
_conversation_history = []   # {"role": "user"|"assistant", "content": str}
_metrics = {"total": 0, "success": 0, "llm_used": 0, "self_corrected": 0, "latencies": []}

# ── LLM ───────────────────────────────────────────────────────────
def llm_available():
    return bool(os.environ.get("GROQ_API_KEY", ""))

# ── Database ──────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect("store.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    global _active_csv_table
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    for (t,) in cur.fetchall():
        cur.execute(f"DROP TABLE IF EXISTS [{t}]")
    conn.commit()
    conn.close()
    _active_csv_table = None

# ── Schema helpers ────────────────────────────────────────────────
def get_tables():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = [r[0] for r in cur.fetchall()]
    tables = {}
    show = [_active_csv_table] if _active_csv_table and _active_csv_table in all_tables else all_tables
    for t in show:
        cur.execute(f"PRAGMA table_info([{t}])")
        tables[t] = [row[1] for row in cur.fetchall()]
    conn.close()
    return tables

def get_sample_rows(table: str, n: int = 5) -> list:
    """RAG: fetch sample rows to give LLM real data context."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM [{table}] LIMIT {n}")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []

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
        cur.execute(f"PRAGMA table_info([{t}])")
        fields = [c[1] + (" (PK)" if c[5] else "") for c in cur.fetchall()]
        nodes.append({"id": t, "label": t, "color": colors[i % len(colors)], "fields": fields})
        cur.execute(f"PRAGMA foreign_key_list([{t}])")
        for fk in cur.fetchall():
            edges.append({"from": fk[2], "to": t, "label": f"{fk[4]} → {fk[3]}"})
    conn.close()
    return {"nodes": nodes, "edges": edges}

# ── SQL helpers ───────────────────────────────────────────────────
def is_valid_sql(sql):
    if not sql or len(sql) < 6:
        return False
    cleaned = sql.strip().upper()
    if not cleaned.startswith("SELECT"):
        return False
    if "FROM" not in cleaned:
        return False
    if cleaned.count("GROUP BY") > 3 or cleaned.count("ORDER BY") > 3:
        return False
    if re.search(r'\bSELECT\s+(MAX|MIN|SUM|AVG|COUNT)\s+FROM\b', sql, re.IGNORECASE):
        return False
    if "[" not in sql:
        log.warning(f"[VALIDATE] SQL missing bracket-wrapped names, rejecting: {sql}")
        return False
    return True

def fix_sql(sql):
    sql = sql.strip().rstrip(';')
    sql = re.sub(r'\s+', ' ', sql)
    if re.match(r'^ELECT\b', sql, re.IGNORECASE):
        sql = 'S' + sql
    return sql + ';'

def is_safe(sql):
    dangerous = ["DELETE", "DROP", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "CREATE", "EXEC", "PRAGMA"]
    upper = sql.upper()
    return not any(word in upper for word in dangerous)

def exec_sql(sql):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

# ── SQL Generation (LLM + ReAct + RAG + self-correction) ─────────
def generate_sql(question, active_table=None):
    global _metrics
    q = question.lower().strip()
    tables = get_tables()

    # Raw SQL passthrough
    if q.lstrip().startswith("select"):
        return fix_sql(question)

    target = active_table if (active_table and active_table in tables) \
             else (list(tables.keys())[0] if tables else None)
    if not target:
        raise ValueError("No table found. Upload a CSV or use the default data.")

    cols = tables[target]

    # ── LLM path: ReAct + RAG ────────────────────────────────────
    if llm_available():
        from llm_sql import generate_sql_llm, self_correct_sql
        sample_rows = get_sample_rows(target)
        log.debug(f"[LLM] ReAct query: '{question}' | table: '{target}' | RAG rows: {len(sample_rows)}")

        sql = generate_sql_llm(
            question, tables, target,
            sample_rows=sample_rows,
            conversation_history=_conversation_history
        )
        _metrics["llm_used"] += 1

        if sql and is_valid_sql(sql):
            fixed = fix_sql(sql)
            # ── Self-correction loop (up to 2 retries) ───────────
            for attempt in range(2):
                try:
                    exec_sql(fixed)   # dry-run to catch errors
                    log.info(f"[LLM] ✅ {fixed}")
                    return fixed
                except sqlite3.Error as e:
                    log.warning(f"[SELF-CORRECT] Attempt {attempt+1} failed: {e}")
                    _metrics["self_corrected"] += 1
                    fixed = fix_sql(self_correct_sql(fixed, str(e), tables, target))
                    if not is_valid_sql(fixed):
                        break
            # Final attempt after corrections
            try:
                exec_sql(fixed)
                return fixed
            except Exception:
                pass

        log.warning(f"[LLM] ❌ Fell back to rules. Got: {sql}")

    # ── Rule-based fallback ───────────────────────────────────────
    log.info(f"[RULES] Fallback for: '{question}' | table: '{target}'")
    t = target

    num_col  = find_col(cols, 'amount','price','salary','revenue','sales','total',
                        'cost','value','score','qty','quantity','marks','grade',
                        'age','enrollment','count','number','duration','days',
                        'temperature','fuel_price','markdown','cpi','unemployment')
    name_col = find_col(cols, 'name','title','label','product','item','employee',
                        'customer','user','student','person','sponsor','store')
    date_col = find_col(cols, 'date','time','created','updated','timestamp','year','month',
                        'start_date','completion_date','start_year')
    status_col = find_col(cols, 'status','state','stage','phase','flag','type','isholiday')

    def num_cast(c):
        return f"CAST([{c}] AS REAL)"

    rules = [
        (r"how many|count all|total count|number of records|number of rows",
            f"SELECT COUNT(*) AS count FROM [{t}];"),
        (r"show all|list all|all records|all data|display all|get all|fetch all|everything",
            f"SELECT * FROM [{t}];"),
    ]
    if num_col:
        rules += [
            (r"\btotal\b|\bsum\b",
                f"SELECT SUM({num_cast(num_col)}) AS total FROM [{t}];"),
            (r"\baverage\b|\bavg\b|\bmean\b",
                f"SELECT AVG({num_cast(num_col)}) AS average FROM [{t}];"),
            (r"\bhighest\b|\bmaximum\b|\bmax\b|\blargest\b|\bbiggest\b",
                f"SELECT * FROM [{t}] ORDER BY {num_cast(num_col)} DESC LIMIT 1;"),
            (r"\blowest\b|\bminimum\b|\bmin\b|\bsmallest\b",
                f"SELECT * FROM [{t}] ORDER BY {num_cast(num_col)} ASC LIMIT 1;"),
            (r"above (\d+(?:\.\d+)?)|greater than (\d+(?:\.\d+)?)|more than (\d+(?:\.\d+)?)",
                lambda m, _t=t, _c=num_col:
                    f"SELECT * FROM [{_t}] WHERE {num_cast(_c)} > {m.group(1) or m.group(2) or m.group(3)};"),
            (r"below (\d+(?:\.\d+)?)|less than (\d+(?:\.\d+)?)|under (\d+(?:\.\d+)?)",
                lambda m, _t=t, _c=num_col:
                    f"SELECT * FROM [{_t}] WHERE {num_cast(_c)} < {m.group(1) or m.group(2) or m.group(3)};"),
            (r"\bsort\b|\border by\b|\bhighest first\b",
                f"SELECT * FROM [{t}] ORDER BY {num_cast(num_col)} DESC;"),
            (r"\blowest first\b|\bascending\b",
                f"SELECT * FROM [{t}] ORDER BY {num_cast(num_col)} ASC;"),
        ]
    if status_col:
        rules += [
            (r"\bcompleted?\b",
                f"SELECT * FROM [{t}] WHERE [{status_col}] LIKE '%complet%';"),
            (r"\brecruiting\b|\bactive\b|\bongoing\b",
                f"SELECT * FROM [{t}] WHERE [{status_col}] LIKE '%recruit%' OR [{status_col}] LIKE '%active%';"),
            (r"\bterminated\b|\bcancelled?\b",
                f"SELECT * FROM [{t}] WHERE [{status_col}] LIKE '%terminat%';"),
            (r"\bholiday\b",
                f"SELECT * FROM [{t}] WHERE [{status_col}] LIKE '%1%' OR [{status_col}] LIKE '%true%';"),
        ]
    phase_col = find_col(cols, 'phase')
    if phase_col:
        rules += [
            (r"phase\s*([1-4])",
                lambda m, _t=t, _p=phase_col:
                    f"SELECT * FROM [{_t}] WHERE [{_p}] LIKE '%{m.group(1)}%';"),
        ]
    if name_col and num_col:
        rules += [
            (r"top\s*(\d+)",
                lambda m, _t=t, _n=name_col, _c=num_col:
                    f"SELECT [{_n}], {num_cast(_c)} AS value FROM [{_t}] ORDER BY {num_cast(_c)} DESC LIMIT {m.group(1)};"),
            (r"bottom\s*(\d+)",
                lambda m, _t=t, _n=name_col, _c=num_col:
                    f"SELECT [{_n}], {num_cast(_c)} AS value FROM [{_t}] ORDER BY {num_cast(_c)} ASC LIMIT {m.group(1)};"),
            (r"\btop\b|\bbest\b",
                f"SELECT [{name_col}], {num_cast(num_col)} AS value FROM [{t}] ORDER BY {num_cast(num_col)} DESC LIMIT 5;"),
        ]
    if name_col:
        rules += [
            (r"\bgroup\b|\bper\b|\beach\b",
                f"SELECT [{name_col}], COUNT(*) AS count FROM [{t}] GROUP BY [{name_col}] ORDER BY count DESC;"),
        ]
    if date_col:
        rules += [
            (r"\brecent\b|\blatest\b|\bnewest\b",
                f"SELECT * FROM [{t}] ORDER BY [{date_col}] DESC LIMIT 10;"),
            (r"\boldest\b|\bearliest\b",
                f"SELECT * FROM [{t}] ORDER BY [{date_col}] ASC LIMIT 10;"),
            (r"\b(20\d\d)\b",
                lambda m, _t=t, _d=date_col:
                    f"SELECT * FROM [{_t}] WHERE [{_d}] LIKE '%{m.group(1)}%';"),
            (r"\bmonthly\b",
                f"SELECT strftime('%Y-%m', [{date_col}]) AS month, COUNT(*) AS count "
                f"FROM [{t}] GROUP BY month ORDER BY month;"),
        ]
    if name_col:
        rules += [
            (r'(?:^|\s)(?:find|search)\s+(.+)',
                lambda m, _t=t, _n=name_col:
                    f"SELECT * FROM [{_t}] WHERE [{_n}] LIKE '%{m.group(1).strip()}%' LIMIT 20;"),
            (r"\bunique\b|\bdistinct\b",
                f"SELECT DISTINCT [{name_col}] FROM [{t}] ORDER BY [{name_col}];"),
        ]
    rules += [
        (r"first\s*(\d+)|show\s*(\d+)|limit\s*(\d+)",
            lambda m, _t=t:
                f"SELECT * FROM [{_t}] LIMIT {m.group(1) or m.group(2) or m.group(3)};"),
        (r".*", f"SELECT * FROM [{t}] LIMIT 50;"),
    ]

    for pattern, sql in rules:
        m = re.search(pattern, q)
        if m:
            result = sql(m) if callable(sql) else sql
            log.info(f"[RULES] Matched '{pattern}' → {result}")
            return result

    return f"SELECT * FROM [{t}] LIMIT 50;"

# ── Explanation ───────────────────────────────────────────────────
def explain_sql(sql, question):
    if llm_available():
        try:
            from llm_sql import get_client
            response = get_client().chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content":
                        "You are a SQL teacher. Explain what the SQL query does in plain English. "
                        "Return a JSON object with exactly two keys: "
                        "'summary' (one sentence) and 'steps' (array of strings, one per SQL clause). "
                        "No markdown, just raw JSON."},
                    {"role": "user", "content": f"Question: {question}\nSQL: {sql}"}
                ],
                temperature=0,
                max_tokens=400,
            )
            import json as _json
            raw = re.sub(r"```json|```", "", response.choices[0].message.content.strip()).strip()
            data = _json.loads(raw)
            if "summary" in data and "steps" in data:
                return data
        except Exception as e:
            log.warning(f"[EXPLAIN] Failed: {e}")

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
    if "GROUP BY" in u: steps.append("GROUP BY — groups rows for aggregation")
    if "SUM(" in u:   summary = "Calculates total sum";   steps.append("SUM() — adds up values")
    if "AVG(" in u:   summary = "Calculates average";     steps.append("AVG() — computes average")
    if "COUNT(" in u: steps.append("COUNT() — counts rows")
    if "MAX(" in u:   steps.append("MAX() — finds the highest value")
    if "MIN(" in u:   steps.append("MIN() — finds the lowest value")
    if "ORDER BY" in u:
        steps.append(f"ORDER BY — sorts {'descending' if 'DESC' in u else 'ascending'}")
    if "LIMIT" in u:
        m = re.search(r"LIMIT\s+(\d+)", u)
        steps.append(f"LIMIT {m.group(1) if m else 'n'} — returns only top rows")
    if not steps:
        steps.append("Fetches matching rows from the database")
    return {"summary": summary, "steps": steps}

# ── Routes ────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/status")
def status():
    return jsonify({"model_ready": True, "llm_active": llm_available()})

@app.route("/metrics")
def metrics():
    m = _metrics
    total = m["total"] or 1
    return jsonify({
        "total_queries": m["total"],
        "success_rate": round(m["success"] / total * 100, 1),
        "llm_used": m["llm_used"],
        "self_corrections": m["self_corrected"],
        "avg_latency_ms": round(sum(m["latencies"]) / len(m["latencies"]) * 1000) if m["latencies"] else 0,
    })

@app.route("/query", methods=["POST"])
def query():
    global _conversation_history, _metrics
    data = request.json or {}
    question = (data.get("question") or "").strip()
    active_table = (data.get("active_table") or "").strip() or None
    if not question:
        return jsonify({"error": "Please enter a question"}), 400
    if len(question) > 500:
        return jsonify({"error": "Question too long (max 500 chars)"}), 400

    _metrics["total"] += 1
    t_start = time.time()
    sql = None

    try:
        log.info(f"[QUERY] '{question}' | table: '{active_table}'")
        sql = generate_sql(question, active_table)
        log.info(f"[QUERY] Final SQL: {sql}")

        if not is_safe(sql):
            return jsonify({"error": "Only SELECT queries are allowed"}), 403

        results = exec_sql(sql)
        latency = time.time() - t_start
        _metrics["success"] += 1
        _metrics["latencies"].append(latency)
        if len(_metrics["latencies"]) > 100:
            _metrics["latencies"] = _metrics["latencies"][-100:]

        log.info(f"[QUERY] ✅ {len(results)} rows | {latency*1000:.0f}ms")

        # ── LLM extras ───────────────────────────────────────────
        insights, followups, chart_type = [], [], "bar"
        if llm_available():
            try:
                from llm_sql import generate_insights, generate_followups, suggest_chart_type, classify_intent
                tables = get_tables()
                insights   = generate_insights(question, sql, results)
                followups  = generate_followups(question, sql, tables, active_table or "")
                chart_type = suggest_chart_type(question, sql, results)
            except Exception as e:
                log.warning(f"[EXTRAS] Failed: {e}")

        # ── Update structured conversation memory ─────────────────
        _conversation_history.append({"role": "user", "content": question})
        _conversation_history.append({
            "role": "assistant",
            "content": f"Ran: {sql.strip()} → {len(results)} rows returned"
        })
        if len(_conversation_history) > 20:
            _conversation_history = _conversation_history[-20:]

        return jsonify({
            "sql": sql,
            "results": results,
            "explanation": explain_sql(sql, question),
            "schema": get_schema(),
            "count": len(results),
            "insights": insights,
            "followups": followups,
            "chart_type": chart_type,
            "latency_ms": round(latency * 1000),
        })

    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except sqlite3.Error as e:
        log.error(f"[QUERY] SQLite error: {e} | SQL: {sql}")
        return jsonify({"error": f"SQL error: {e}", "sql": sql or ""}), 422
    except Exception as e:
        log.error(f"[QUERY] Error: {e}")
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
        table_name = re.sub(r"[^a-z0-9_]", "_", f.filename.replace(".csv", "").lower())
        cols = list(rows[0].keys())
        conn = sqlite3.connect("store.db")
        cur = conn.cursor()
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
        log.info(f"[UPLOAD] ✅ '{table_name}' | {len(rows)} rows | cols: {cols}")
        return jsonify({
            "message": f"Table '{table_name}' created with {len(rows)} rows",
            "table": table_name, "columns": cols, "preview": rows[:5]
        })
    except Exception as e:
        log.error(f"[UPLOAD] Failed: {e}")
        return jsonify({"error": f"Failed to process CSV: {e}"}), 500

@app.route("/clear-db", methods=["POST"])
def clear_db():
    global _active_csv_table, _conversation_history
    conn = sqlite3.connect("store.db")
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    for (t,) in cur.fetchall():
        cur.execute(f"DROP TABLE IF EXISTS [{t}]")
    conn.commit()
    conn.close()
    _active_csv_table = None
    _conversation_history = []
    return jsonify({"message": "Database cleared"})

@app.route("/clear-history", methods=["POST"])
def clear_history():
    global _conversation_history
    _conversation_history = []
    return jsonify({"message": "Conversation history cleared"})

@app.route("/chart-data", methods=["POST"])
def chart_data():
    data = request.json or {}
    results = data.get("results", [])
    suggested_type = data.get("chart_type", None)
    if not results:
        return jsonify({"error": "No data"}), 400

    keys = list(results[0].keys())

    def is_numeric(val):
        try: float(str(val).replace(',','')); return True
        except: return False

    label_col = next((k for k in keys if not is_numeric(results[0].get(k,''))), keys[0])
    value_col = next((k for k in keys if k != label_col and is_numeric(results[0].get(k,''))), None)

    if not value_col:
        from collections import Counter
        label_col = keys[0]
        counts = Counter(str(r.get(label_col,'')) for r in results)
        labels = list(counts.keys())[:20]
        values = [counts[l] for l in labels]
        value_col = "count"
    else:
        labels = [str(r.get(label_col, ''))[:25] for r in results[:20]]
        values = []
        for r in results[:20]:
            try: values.append(float(str(r.get(value_col, 0) or 0).replace(',','')))
            except: values.append(0)

    # Use LLM-suggested chart type if provided, else heuristic
    if suggested_type and suggested_type in ("bar", "line", "pie", "stat"):
        chart_type = suggested_type
    else:
        chart_type = "bar"
        if len(labels) <= 6: chart_type = "pie"
        if label_col.lower() in ['month','year','date','time','start_year']: chart_type = "line"

    return jsonify({
        "labels": labels, "values": values,
        "label_col": label_col, "value_col": value_col,
        "chart_type": chart_type
    })

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
    print(f"🤖 LLM: {'✅ Groq (Llama 3.3 70B) — ReAct + RAG + Self-Correction' if llm_available() else '⚠️  No GROQ_API_KEY — rule-based fallback active'}")
    app.run(debug=True)
