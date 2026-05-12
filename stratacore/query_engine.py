import json
import logging
import re
import sqlite3

from stratacore.config import llm_available
from stratacore.core_state import CoreState
from stratacore.db import get_sample_rows, get_tables
from stratacore import llm_sql
from stratacore.sql_ops import exec_sql, fix_sql, is_valid_sql

log = logging.getLogger(__name__)


def find_col(cols: list[str], *hints: str) -> str | None:
    for hint in hints:
        for c in cols:
            if hint.lower() in c.lower():
                return c
    return cols[0] if cols else None


def generate_sql(question: str, active_table: str | None, core: CoreState) -> str:
    q = question.lower().strip()
    tables = get_tables(core.active_csv_table)

    if q.lstrip().startswith("select"):
        return fix_sql(question)

    target = (
        active_table
        if (active_table and active_table in tables)
        else (list(tables.keys())[0] if tables else None)
    )
    if not target:
        raise ValueError("No table found. Upload a CSV or Excel file to get started.")

    cols = tables[target]

    if llm_available():
        sample_rows = get_sample_rows(target)
        log.debug("[LLM] ReAct query: '%s' | table: '%s' | RAG rows: %s", question, target, len(sample_rows))

        sql = llm_sql.generate_sql_llm(
            question,
            tables,
            target,
            sample_rows=sample_rows,
            conversation_history=core.conversation_history,
        )
        core.metrics["llm_used"] += 1

        if sql and is_valid_sql(sql):
            fixed = fix_sql(sql)
            for _attempt in range(2):
                try:
                    exec_sql(fixed)
                    log.info("[LLM] OK %s", fixed)
                    return fixed
                except sqlite3.Error as e:
                    log.warning("[SELF-CORRECT] failed: %s", e)
                    core.metrics["self_corrected"] += 1
                    fixed = fix_sql(llm_sql.self_correct_sql(fixed, str(e), tables, target))
                    if not is_valid_sql(fixed):
                        break
            try:
                exec_sql(fixed)
                return fixed
            except Exception:
                pass

        log.warning("[LLM] Fell back to rules. Got: %s", sql)

    log.info("[RULES] Fallback for: '%s' | table: '%s'", question, target)
    t = target

    num_col = find_col(
        cols,
        "amount",
        "price",
        "salary",
        "revenue",
        "sales",
        "total",
        "cost",
        "value",
        "score",
        "qty",
        "quantity",
        "marks",
        "grade",
        "age",
        "enrollment",
        "count",
        "number",
        "duration",
        "days",
        "temperature",
        "fuel_price",
        "markdown",
        "cpi",
        "unemployment",
    )
    name_col = find_col(
        cols,
        "name",
        "title",
        "label",
        "product",
        "item",
        "employee",
        "customer",
        "user",
        "student",
        "person",
        "sponsor",
        "store",
    )
    date_col = find_col(
        cols,
        "date",
        "time",
        "created",
        "updated",
        "timestamp",
        "year",
        "month",
        "start_date",
        "completion_date",
        "start_year",
    )
    status_col = find_col(cols, "status", "state", "stage", "phase", "flag", "type", "isholiday")

    def num_cast(c: str) -> str:
        return f"CAST([{c}] AS REAL)"

    rules: list = [
        (
            r"how many|count all|total count|number of records|number of rows",
            f"SELECT COUNT(*) AS count FROM [{t}];",
        ),
        (
            r"show all|list all|all records|all data|display all|get all|fetch all|everything",
            f"SELECT * FROM [{t}];",
        ),
    ]
    if num_col:
        rules += [
            (r"\btotal\b|\bsum\b", f"SELECT SUM({num_cast(num_col)}) AS total FROM [{t}];"),
            (r"\baverage\b|\bavg\b|\bmean\b", f"SELECT AVG({num_cast(num_col)}) AS average FROM [{t}];"),
            (
                r"\bhighest\b|\bmaximum\b|\bmax\b|\blargest\b|\bbiggest\b",
                f"SELECT * FROM [{t}] ORDER BY {num_cast(num_col)} DESC LIMIT 1;",
            ),
            (
                r"\blowest\b|\bminimum\b|\bmin\b|\bsmallest\b",
                f"SELECT * FROM [{t}] ORDER BY {num_cast(num_col)} ASC LIMIT 1;",
            ),
            (
                r"above (\d+(?:\.\d+)?)|greater than (\d+(?:\.\d+)?)|more than (\d+(?:\.\d+)?)",
                lambda m, _t=t, _c=num_col: (
                    f"SELECT * FROM [{_t}] WHERE {num_cast(_c)} > "
                    f"{m.group(1) or m.group(2) or m.group(3)};"
                ),
            ),
            (
                r"below (\d+(?:\.\d+)?)|less than (\d+(?:\.\d+)?)|under (\d+(?:\.\d+)?)",
                lambda m, _t=t, _c=num_col: (
                    f"SELECT * FROM [{_t}] WHERE {num_cast(_c)} < "
                    f"{m.group(1) or m.group(2) or m.group(3)};"
                ),
            ),
            (
                r"\bsort\b|\border by\b|\bhighest first\b",
                f"SELECT * FROM [{t}] ORDER BY {num_cast(num_col)} DESC;",
            ),
            (
                r"\blowest first\b|\bascending\b",
                f"SELECT * FROM [{t}] ORDER BY {num_cast(num_col)} ASC;",
            ),
        ]
    if status_col:
        rules += [
            (r"\bcompleted?\b", f"SELECT * FROM [{t}] WHERE [{status_col}] LIKE '%complet%';"),
            (
                r"\brecruiting\b|\bactive\b|\bongoing\b",
                f"SELECT * FROM [{t}] WHERE [{status_col}] LIKE '%recruit%' OR [{status_col}] LIKE '%active%';",
            ),
            (
                r"\bterminated\b|\bcancelled?\b",
                f"SELECT * FROM [{t}] WHERE [{status_col}] LIKE '%terminat%';",
            ),
            (r"\bholiday\b", f"SELECT * FROM [{t}] WHERE [{status_col}] LIKE '%1%' OR [{status_col}] LIKE '%true%';"),
        ]
    phase_col = find_col(cols, "phase")
    if phase_col:
        rules += [
            (
                r"phase\s*([1-4])",
                lambda m, _t=t, _p=phase_col: f"SELECT * FROM [{_t}] WHERE [{_p}] LIKE '%{m.group(1)}%';",
            ),
        ]
    if name_col and num_col:
        rules += [
            (
                r"top\s*(\d+)",
                lambda m, _t=t, _n=name_col, _c=num_col: (
                    f"SELECT [{_n}], {num_cast(_c)} AS value FROM [{_t}] ORDER BY {num_cast(_c)} "
                    f"DESC LIMIT {m.group(1)};"
                ),
            ),
            (
                r"bottom\s*(\d+)",
                lambda m, _t=t, _n=name_col, _c=num_col: (
                    f"SELECT [{_n}], {num_cast(_c)} AS value FROM [{_t}] ORDER BY {num_cast(_c)} "
                    f"ASC LIMIT {m.group(1)};"
                ),
            ),
            (
                r"\btop\b|\bbest\b",
                f"SELECT [{name_col}], {num_cast(num_col)} AS value FROM [{t}] ORDER BY {num_cast(num_col)} DESC LIMIT 5;",
            ),
        ]
    if name_col:
        rules += [
            (
                r"\bgroup\b|\bper\b|\beach\b",
                f"SELECT [{name_col}], COUNT(*) AS count FROM [{t}] GROUP BY [{name_col}] ORDER BY count DESC;",
            ),
        ]
    if date_col:
        rules += [
            (r"\brecent\b|\blatest\b|\bnewest\b", f"SELECT * FROM [{t}] ORDER BY [{date_col}] DESC LIMIT 10;"),
            (r"\boldest\b|\bearliest\b", f"SELECT * FROM [{t}] ORDER BY [{date_col}] ASC LIMIT 10;"),
            (
                r"\b(20\d\d)\b",
                lambda m, _t=t, _d=date_col: f"SELECT * FROM [{_t}] WHERE [{_d}] LIKE '%{m.group(1)}%';",
            ),
            (
                r"\bmonthly\b",
                f"SELECT strftime('%Y-%m', [{date_col}]) AS month, COUNT(*) AS count "
                f"FROM [{t}] GROUP BY month ORDER BY month;",
            ),
        ]
    if name_col:
        rules += [
            (
                r"(?:^|\s)(?:find|search)\s+(.+)",
                lambda m, _t=t, _n=name_col: f"SELECT * FROM [{_t}] WHERE [{_n}] LIKE '%{m.group(1).strip()}%' LIMIT 20;",
            ),
            (r"\bunique\b|\bdistinct\b", f"SELECT DISTINCT [{name_col}] FROM [{t}] ORDER BY [{name_col}];"),
        ]
    rules += [
        (
            r"first\s*(\d+)|show\s*(\d+)|limit\s*(\d+)",
            lambda m, _t=t: f"SELECT * FROM [{_t}] LIMIT {m.group(1) or m.group(2) or m.group(3)};",
        ),
        (r".*", f"SELECT * FROM [{t}] LIMIT 50;"),
    ]

    for pattern, sql in rules:
        m = re.search(pattern, q)
        if m:
            result = sql(m) if callable(sql) else sql
            log.info("[RULES] Matched '%s' → %s", pattern, result)
            return result

    return f"SELECT * FROM [{t}] LIMIT 50;"


def explain_sql(sql: str, question: str) -> dict:
    if llm_available():
        try:
            response = llm_sql.get_client().chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a SQL teacher. Explain what the SQL query does in plain English. "
                            "Return a JSON object with exactly two keys: "
                            "'summary' (one sentence) and 'steps' (array of strings, one per SQL clause). "
                            "No markdown, just raw JSON."
                        ),
                    },
                    {"role": "user", "content": f"Question: {question}\nSQL: {sql}"},
                ],
                temperature=0,
                max_tokens=400,
            )
            raw = re.sub(r"```json|```", "", response.choices[0].message.content.strip()).strip()
            data = json.loads(raw)
            if "summary" in data and "steps" in data:
                return data
        except Exception as e:
            log.warning("[EXPLAIN] Failed: %s", e)

    u = sql.upper()
    steps: list[str] = []
    summary = f"Retrieves data based on: '{question}'"
    if "SELECT" in u:
        m = re.search(r"SELECT\s+(.+?)\s+FROM", u)
        col = m.group(1) if m else "*"
        steps.append(
            f"SELECT {col} — fetches {'all columns' if col.strip() == '*' else 'specific columns'}"
        )
    if "JOIN" in u:
        steps.append("JOIN — combines rows from multiple tables")
    if "WHERE" in u:
        m = re.search(r"WHERE\s+(.+?)(?:\s+GROUP|\s+ORDER|\s+LIMIT|$)", u)
        if m:
            steps.append(f"WHERE {m.group(1).strip()} — filters rows")
    if "GROUP BY" in u:
        steps.append("GROUP BY — groups rows for aggregation")
    if "SUM(" in u:
        summary = "Calculates total sum"
        steps.append("SUM() — adds up values")
    if "AVG(" in u:
        summary = "Calculates average"
        steps.append("AVG() — computes average")
    if "COUNT(" in u:
        steps.append("COUNT() — counts rows")
    if "MAX(" in u:
        steps.append("MAX() — finds the highest value")
    if "MIN(" in u:
        steps.append("MIN() — finds the lowest value")
    if "ORDER BY" in u:
        steps.append(f"ORDER BY — sorts {'descending' if 'DESC' in u else 'ascending'}")
    if "LIMIT" in u:
        m = re.search(r"LIMIT\s+(\d+)", u)
        steps.append(f"LIMIT {m.group(1) if m else 'n'} — returns only top rows")
    if not steps:
        steps.append("Fetches matching rows from the database")
    return {"summary": summary, "steps": steps}
