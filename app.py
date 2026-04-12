from flask import Flask, render_template, request, jsonify
import sqlite3
import re
import os
import io
import csv

app = Flask(__name__)

MODEL_PATH = "my_sql_model"
_tokenizer = None
_model = None

def load_model():
    global _tokenizer, _model
    if _model is not None:
        return
    if not os.path.exists(MODEL_PATH):
        raise RuntimeError("Model not found. Please run: python train_model.py")
    from transformers import T5Tokenizer, T5ForConditionalGeneration
    import torch
    print(f"⏳ Loading model from {MODEL_PATH}...")
    _tokenizer = T5Tokenizer.from_pretrained(MODEL_PATH)
    _model = T5ForConditionalGeneration.from_pretrained(MODEL_PATH)
    _model.eval()
    print("✅ Model ready!")

def is_model_ready():
    return os.path.exists(MODEL_PATH)

# ── Database ──────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect("store.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY, customer_id INTEGER,
        amount REAL, date TEXT,
        FOREIGN KEY (customer_id) REFERENCES customers(id))""")
    cur.execute("SELECT COUNT(*) FROM customers")
    if cur.fetchone()[0] == 0:
        cur.executemany("INSERT INTO customers VALUES (?, ?)", [
            (1,"Alice Johnson"),(2,"Bob Smith"),(3,"Carol White"),
            (4,"David Lee"),(5,"Eva Martinez"),(6,"Frank Chen"),
        ])
        cur.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", [
            (1,1,1500.00,"2024-01-15"),(2,1,2300.00,"2024-02-20"),
            (3,2,800.00,"2024-01-10"),(4,3,4200.00,"2024-03-05"),
            (5,3,1100.00,"2024-03-18"),(6,4,600.00,"2024-02-28"),
            (7,5,3500.00,"2024-01-22"),(8,5,2100.00,"2024-04-01"),
            (9,6,950.00,"2024-03-30"),(10,2,1750.00,"2024-04-10"),
        ])
        conn.commit()
    conn.close()

# ── Dynamic schema helpers ────────────────────────────────────────
def get_tables():
    """Returns {table: [col, ...]} for all tables in DB."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {}
    for (t,) in cur.fetchall():
        cur.execute(f"PRAGMA table_info([{t}])")
        tables[t] = [row[1] for row in cur.fetchall()]
    conn.close()
    return tables

def find_table(tables, *hints):
    """Find a table name that matches any of the hint keywords."""
    for hint in hints:
        for t in tables:
            if hint.lower() in t.lower():
                return t
    return list(tables.keys())[0] if tables else None

def find_col(cols, *hints):
    """Find a column name that matches any hint keyword."""
    for hint in hints:
        for c in cols:
            if hint.lower() in c.lower():
                return c
    return cols[0] if cols else None

# ── Dynamic rule-based SQL ────────────────────────────────────────
def rule_based_sql(question):
    q = question.lower().strip()
    tables = get_tables()
    if not tables:
        return None

    # Detect which table the user is asking about
    target_table = None
    for t in tables:
        if t.lower() in q or t.lower().rstrip('s') in q:
            target_table = t
            break
    if not target_table:
        target_table = list(tables.keys())[0]

    cols = tables[target_table]

    # Find numeric column for aggregates
    num_col = find_col(cols, 'amount','price','salary','revenue','sales','total','cost','value','score','qty','quantity')
    # Find name/text column
    name_col = find_col(cols, 'name','title','label','product','item','employee','customer','user')
    # Find date column
    date_col = find_col(cols, 'date','time','created','updated','timestamp')
    # Find id column
    id_col = find_col(cols, 'id')

    # Generic patterns that work on any table
    rules = []

    if num_col:
        rules += [
            (r"total|sum|revenue|sales", f"SELECT SUM([{num_col}]) as total FROM [{target_table}];"),
            (r"average|avg|mean",        f"SELECT AVG([{num_col}]) as average FROM [{target_table}];"),
            (r"highest|max|maximum",     f"SELECT MAX([{num_col}]) as max_value FROM [{target_table}];"),
            (r"lowest|min|minimum",      f"SELECT MIN([{num_col}]) as min_value FROM [{target_table}];"),
            (r"above (\d+)|greater than (\d+)|more than (\d+)",
                lambda m, t=target_table, c=num_col:
                    f"SELECT * FROM [{t}] WHERE [{c}] > {m.group(1) or m.group(2) or m.group(3)};"),
            (r"below (\d+)|less than (\d+)",
                lambda m, t=target_table, c=num_col:
                    f"SELECT * FROM [{t}] WHERE [{c}] < {m.group(1) or m.group(2)};"),
            (r"sort.*amount|order.*amount|highest first",
                f"SELECT * FROM [{target_table}] ORDER BY [{num_col}] DESC;"),
            (r"cheapest|lowest.*first|ascending",
                f"SELECT * FROM [{target_table}] ORDER BY [{num_col}] ASC;"),
        ]

    if date_col:
        rules += [
            (r"recent|latest|newest",
                f"SELECT * FROM [{target_table}] ORDER BY [{date_col}] DESC LIMIT 10;"),
            (r"oldest|earliest",
                f"SELECT * FROM [{target_table}] ORDER BY [{date_col}] ASC LIMIT 10;"),
            (r"in 2024|2024",
                f"SELECT * FROM [{target_table}] WHERE [{date_col}] LIKE '2024%';"),
            (r"in 2023|2023",
                f"SELECT * FROM [{target_table}] WHERE [{date_col}] LIKE '2023%';"),
            (r"in january|in jan",
                f"SELECT * FROM [{target_table}] WHERE [{date_col}] LIKE '%-01-%';"),
            (r"in february|in feb",
                f"SELECT * FROM [{target_table}] WHERE [{date_col}] LIKE '%-02-%';"),
            (r"in march|in mar",
                f"SELECT * FROM [{target_table}] WHERE [{date_col}] LIKE '%-03-%';"),
            (r"in april|in apr",
                f"SELECT * FROM [{target_table}] WHERE [{date_col}] LIKE '%-04-%';"),
            (r"monthly",
                f"SELECT strftime('%Y-%m', [{date_col}]) as month, COUNT(*) as count "
                f"FROM [{target_table}] GROUP BY month ORDER BY month;"),
        ]
        if num_col:
            rules += [
                (r"monthly.*revenue|monthly.*total|revenue.*month",
                    f"SELECT strftime('%Y-%m', [{date_col}]) as month, SUM([{num_col}]) as total "
                    f"FROM [{target_table}] GROUP BY month ORDER BY month;"),
            ]

    if name_col and num_col:
        rules += [
            (r"top\s*(\d+)",
                lambda m, t=target_table, n=name_col, c=num_col:
                    f"SELECT [{n}], SUM([{c}]) as total FROM [{t}] "
                    f"GROUP BY [{n}] ORDER BY total DESC LIMIT {m.group(1)};"),
            (r"top|best|highest.*name|most",
                f"SELECT [{name_col}], SUM([{num_col}]) as total FROM [{target_table}] "
                f"GROUP BY [{name_col}] ORDER BY total DESC LIMIT 1;"),
            (r"per {name_col}|by {name_col}|each".replace('{name_col}', name_col.lower()),
                f"SELECT [{name_col}], SUM([{num_col}]) as total FROM [{target_table}] "
                f"GROUP BY [{name_col}];"),
        ]

    # Count / list always work
    rules += [
        (r"how many|count",  f"SELECT COUNT(*) as count FROM [{target_table}];"),
        (r"all|list|show|display|every", f"SELECT * FROM [{target_table}];"),
    ]

    for pattern, sql in rules:
        m = re.search(pattern, q)
        if m:
            return sql(m) if callable(sql) else sql

    return None

def is_valid_sql(sql):
    """Check if model output looks like real SQL and not a hallucination loop."""
    if not sql or len(sql) < 10:
        return False
    # Repeated GROUP BY / ORDER BY is a hallucination sign
    if sql.upper().count("GROUP BY") > 2 or sql.upper().count("ORDER BY") > 2:
        return False
    # Must start with SELECT
    if not sql.strip().upper().startswith("SELECT"):
        return False
    # FROM must appear
    if "FROM" not in sql.upper():
        return False
    return True

def fix_sql(sql):
    sql = sql.strip().rstrip(';')
    sql = re.sub(r'\s+', ' ', sql)
    return sql + ';'

# ── SQL Generation ────────────────────────────────────────────────
def generate_sql(question):
    # Try rule-based first for reliability
    rule_sql = rule_based_sql(question)

    # Also try model
    try:
        load_model()
        import torch
        input_text = "convert to SQL: " + question.lower().strip()
        inputs = _tokenizer(input_text, return_tensors="pt", max_length=128, truncation=True)
        with torch.no_grad():
            outputs = _model.generate(**inputs, max_length=128, num_beams=4,
                                      early_stopping=True, no_repeat_ngram_size=3)
        raw = _tokenizer.decode(outputs[0], skip_special_tokens=True).strip()
        model_sql = fix_sql(raw)
        if is_valid_sql(model_sql):
            return model_sql
    except Exception:
        pass

    # Fall back to rule-based
    if rule_sql:
        return rule_sql

    raise ValueError("Could not generate SQL for this question. Try rephrasing.")

def explain_sql(sql, question):
    u = sql.upper()
    steps, summary = [], f"Retrieves data based on: '{question}'"
    if "SELECT" in u:
        m = re.search(r"SELECT\s+(.+?)\s+FROM", u)
        col = m.group(1) if m else "*"
        steps.append(f"SELECT {col} — fetches {'all columns' if col.strip()=='*' else 'specific columns'}")
    if "JOIN" in u:
        steps.append("JOIN — links customers and orders via customer_id")
    if "WHERE" in u:
        m = re.search(r"WHERE\s+(.+?)(?:\s+GROUP|\s+ORDER|\s+LIMIT|$)", u)
        if m: steps.append(f"WHERE {m.group(1).strip()} — filters rows")
    if "GROUP BY" in u: steps.append("GROUP BY — groups rows for aggregates")
    if "SUM(" in u: summary = "Calculates total sum"; steps.append("SUM() — adds up amounts")
    if "AVG(" in u: summary = "Calculates average"; steps.append("AVG() — computes average")
    if "COUNT(" in u: steps.append("COUNT() — counts rows")
    if "MAX(" in u: steps.append("MAX() — finds highest value")
    if "MIN(" in u: steps.append("MIN() — finds lowest value")
    if "HAVING" in u:
        m = re.search(r"HAVING\s+(.+?)(?:\s+ORDER|\s+LIMIT|$)", u)
        if m: steps.append(f"HAVING {m.group(1).strip()} — filters after grouping")
    if "ORDER BY" in u:
        steps.append(f"ORDER BY — sorts {'DESC' if 'DESC' in u else 'ASC'}")
    if "LIMIT" in u:
        m = re.search(r"LIMIT\s+(\d+)", u)
        n = m.group(1) if m else "n"
        steps.append(f"LIMIT {n} — returns top {n} rows")
    if not steps: steps.append("Fetches matching data from the database")
    return {"summary": summary, "steps": steps}

def is_safe(sql):
    return not any(w in sql.upper() for w in ["DELETE","DROP","UPDATE","INSERT","ALTER","TRUNCATE","CREATE"])

def exec_sql(sql):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_schema():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    nodes, edges = [], []
    colors = ["#6366f1","#f59e0b","#10b981","#ef4444","#8b5cf6","#06b6d4"]
    for i, t in enumerate(tables):
        cur.execute(f"PRAGMA table_info({t})")
        cols = cur.fetchall()
        fields = []
        for c in cols:
            label = c[1]
            if c[5]: label += " (PK)"
            fields.append(label)
        nodes.append({"id": t, "label": t, "color": colors[i % len(colors)], "fields": fields})
        cur.execute(f"PRAGMA foreign_key_list({t})")
        for fk in cur.fetchall():
            edges.append({"from": fk[2], "to": t, "label": f"{fk[4]} → {fk[3]}"})
    conn.close()
    return {"nodes": nodes, "edges": edges}

# ── Routes ────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/status")
def status():
    return jsonify({"model_ready": is_model_ready()})

@app.route("/query", methods=["POST"])
def query():
    data = request.json or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "Please enter a question"}), 400
    if len(question) > 300:
        return jsonify({"error": "Question too long (max 300 chars)"}), 400
    try:
        sql = generate_sql(question)
        if not sql or len(sql) < 6:
            return jsonify({"error": "Could not generate SQL. Try rephrasing."}), 422
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
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except sqlite3.Error as e:
        return jsonify({"error": f"SQL error: {e}", "sql": sql if 'sql' in locals() else ""}), 422
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
        stream = io.StringIO(f.stream.read().decode("utf-8"), newline=None)
        reader = csv.DictReader(stream)
        rows = list(reader)
        if not rows:
            return jsonify({"error": "CSV file is empty"}), 400

        table_name = re.sub(r"[^a-z0-9_]", "_", f.filename.replace(".csv","").lower())
        cols = list(rows[0].keys())

        conn = sqlite3.connect("store.db")
        cur = conn.cursor()
        # Drop and recreate table
        cur.execute(f"DROP TABLE IF EXISTS [{table_name}]")
        col_defs = ", ".join(f'[{c}] TEXT' for c in cols)
        cur.execute(f"CREATE TABLE [{table_name}] ({col_defs})")
        placeholders = ", ".join("?" * len(cols))
        for row in rows:
            cur.execute(f"INSERT INTO [{table_name}] VALUES ({placeholders})",
                        [row.get(c, "") for c in cols])
        conn.commit()
        conn.close()

        return jsonify({
            "message": f"Table '{table_name}' created with {len(rows)} rows",
            "table": table_name,
            "columns": cols,
            "preview": rows[:5]
        })
    except Exception as e:
        return jsonify({"error": f"Failed to process CSV: {e}"}), 500

@app.route("/schema")
def schema_route():
    return jsonify(get_schema())

@app.route("/tables")
def tables_route():
    """Returns all tables and their columns for the UI hint panel."""
    return jsonify(get_tables())

if __name__ == "__main__":
    init_db()
    print(f"🗄️  Database ready")
    print(f"🤖 Model: {'✅ Ready' if is_model_ready() else '⚠️  Run python train_model.py first'}")
    app.run(debug=True)
