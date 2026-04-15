"""
llm_sql.py — Advanced LLM SQL engine
Features: ReAct reasoning, RAG (sample data), self-correction, intent classification,
          smart chart suggestion, insights, follow-ups, conversational memory
"""

import os
import re
import json
import logging
from groq import Groq

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("groq").setLevel(logging.WARNING)

client = None

def get_client():
    global client
    if client is None:
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set.")
        client = Groq(api_key=api_key)
    return client


# ── Schema + RAG helpers ──────────────────────────────────────────

def build_schema_prompt(tables: dict) -> str:
    lines = []
    for table, cols in tables.items():
        lines.append(f"Table: [{table}]")
        lines.append(f"  Columns: {', '.join(f'[{c}]' for c in cols)}")
    return "\n".join(lines)


def build_rag_context(sample_rows: list, cols: list) -> str:
    """Format sample rows so LLM understands actual data values and types."""
    if not sample_rows:
        return ""
    lines = ["SAMPLE DATA (first few rows — use to understand column meaning and value format):"]
    for i, row in enumerate(sample_rows[:5]):
        lines.append(f"  Row {i+1}: { {c: row.get(c, '') for c in cols} }")
    return "\n".join(lines)


# ── 1. Intent Classification ──────────────────────────────────────

def classify_intent(question: str) -> str:
    """
    Classify query intent: aggregation | filter | trend | comparison | lookup | unknown
    Uses fast rule-based check first, LLM as fallback.
    """
    q = question.lower()
    if re.search(r'\b(total|sum|average|avg|count|how many|maximum|minimum|max|min)\b', q):
        return "aggregation"
    if re.search(r'\b(trend|over time|monthly|yearly|by date|by year|by month)\b', q):
        return "trend"
    if re.search(r'\b(compare|vs|versus|difference|between)\b', q):
        return "comparison"
    if re.search(r'\b(where|filter|only|with|having|greater|less|above|below)\b', q):
        return "filter"
    if re.search(r'\b(show|list|find|search|get|fetch|all|top|bottom)\b', q):
        return "lookup"
    return "aggregation"


# ── 2. ReAct-style SQL generation with RAG ────────────────────────

def generate_sql_llm(question: str, tables: dict, active_table: str = None,
                     sample_rows: list = None, conversation_history: list = None) -> str:
    """
    ReAct-style NL→SQL: Thought → Plan → SQL
    Includes RAG (sample rows) and conversation memory.
    """
    schema_str = build_schema_prompt(tables)
    fallback_table = active_table or list(tables.keys())[0]
    active_cols = tables.get(fallback_table, [])
    col_list = ", ".join(f"[{c}]" for c in active_cols)
    intent = classify_intent(question)
    rag_context = build_rag_context(sample_rows or [], active_cols)

    # Build conversation context block
    conv_block = ""
    if conversation_history:
        recent = conversation_history[-6:]
        conv_block = "CONVERSATION HISTORY (for context resolution):\n"
        conv_block += "\n".join(
            f"  {'User' if h['role']=='user' else 'Assistant'}: {h['content']}"
            for h in recent
        )
        conv_block += "\n"

    system_prompt = f"""You are an expert SQLite data analyst using ReAct reasoning.

ABSOLUTE RULES:
1. Output ONLY the raw SQL query at the end — no explanation, no markdown, no code fences.
2. ALWAYS start with SELECT. NEVER use DELETE, DROP, UPDATE, INSERT, ALTER, CREATE, TRUNCATE.
3. ALWAYS wrap every table and column name in square brackets: [table], [column].
4. ALWAYS end with a semicolon.
5. ONLY use columns that exist in the schema — NEVER invent column names.
6. ALWAYS alias aggregates: COUNT(*) AS count, SUM([col]) AS total, AVG([col]) AS average.
7. For text filtering use LIKE: WHERE [col] LIKE '%value%'.
8. For numeric columns stored as text, cast: CAST([col] AS REAL).
9. If you cannot answer, output: SELECT * FROM [{fallback_table}] LIMIT 10;

DATABASE SCHEMA:
{schema_str}

{rag_context}

FOCUSED TABLE: [{fallback_table}]
AVAILABLE COLUMNS: {col_list}
DETECTED INTENT: {intent}

{conv_block}"""

    user_prompt = f"""Question: {question}

Use this ReAct format to reason, then output ONLY the final SQL:

Thought: What is the user asking for?
Plan: Which columns, aggregations, filters, groupings are needed?
SQL: <final SQL query here>"""

    log.debug(f"[GROQ] ReAct query: '{question}' | intent: {intent}")

    response = get_client().chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0,
        max_tokens=400,
        stop=["Question:"],
    )

    raw = response.choices[0].message.content.strip()
    log.debug(f"[GROQ] Raw ReAct output:\n{raw}")

    sql = _extract_sql_from_react(raw, fallback_table)
    log.debug(f"[GROQ] Extracted SQL: {sql}")
    return sql


def _extract_sql_from_react(raw: str, fallback_table: str) -> str:
    """Extract just the SQL from a ReAct-format response."""
    # Try to find SQL: label
    m = re.search(r'SQL:\s*(SELECT.+?)(?:\n|$)', raw, re.IGNORECASE | re.DOTALL)
    if m:
        sql = m.group(1).strip()
    else:
        # Fallback: find any SELECT statement
        m2 = re.search(r'(SELECT\s.+)', raw, re.IGNORECASE | re.DOTALL)
        sql = m2.group(1).strip() if m2 else f"SELECT * FROM [{fallback_table}] LIMIT 10;"

    sql = re.sub(r"```sql|```", "", sql).strip()
    sql = re.sub(r"^SQL:\s*", "", sql, flags=re.IGNORECASE).strip()
    sql = sql.split(";")[0].strip() + ";"
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE).strip()
    if not sql.endswith(";"):
        sql += ";"
    return sql


# ── 3. Self-Correcting LLM loop ───────────────────────────────────

def self_correct_sql(sql: str, error: str, tables: dict, active_table: str) -> str:
    """
    Given a failed SQL and its error, ask LLM to fix it.
    This is the self-healing loop.
    """
    schema_str = build_schema_prompt(tables)
    fallback_table = active_table or list(tables.keys())[0]
    active_cols = tables.get(fallback_table, [])
    col_list = ", ".join(f"[{c}]" for c in active_cols)

    log.info(f"[SELF-CORRECT] Attempting fix for error: {error}")

    try:
        response = get_client().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content":
                    f"""You are an expert SQLite debugger. Fix the broken SQL query.

RULES: Output ONLY the corrected SQL. No explanation. Must start with SELECT. Use square brackets for all names.

SCHEMA:
{schema_str}

TABLE: [{fallback_table}]
COLUMNS: {col_list}"""},
                {"role": "user", "content":
                    f"Broken SQL:\n{sql}\n\nError message:\n{error}\n\nFixed SQL:"}
            ],
            temperature=0,
            max_tokens=300,
        )
        raw = response.choices[0].message.content.strip()
        fixed = _extract_sql_from_react(raw, fallback_table)
        log.info(f"[SELF-CORRECT] Fixed SQL: {fixed}")
        return fixed
    except Exception as e:
        log.warning(f"[SELF-CORRECT] Failed: {e}")
        return sql


# ── 4. Smart chart type suggestion ───────────────────────────────

def suggest_chart_type(question: str, sql: str, results: list) -> str:
    """
    LLM decides the best chart type based on query intent and result shape.
    Returns: bar | line | pie | stat
    """
    if not results:
        return "bar"
    if len(results) == 1 and len(results[0]) == 1:
        return "stat"

    intent = classify_intent(question)
    col_names = list(results[0].keys()) if results else []

    # Fast heuristic first
    has_date = any(k.lower() in ['date','month','year','time','start_year'] for k in col_names)
    if intent == "trend" or has_date:
        return "line"
    if len(results) <= 6 and intent in ("aggregation", "comparison"):
        return "pie"
    return "bar"


# ── 5. AI Insights ────────────────────────────────────────────────

def generate_insights(question: str, sql: str, results: list) -> list:
    """2-3 specific data insights from query results."""
    if not results:
        return []
    preview = results[:10]
    try:
        response = get_client().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content":
                    "You are a data analyst. Given a question, SQL, and sample results, "
                    "generate 2-3 concise specific insights about what the data reveals. "
                    "Each insight must be one sentence and mention actual values from the data. "
                    "Return a JSON array of strings. No markdown, just raw JSON array."},
                {"role": "user", "content":
                    f"Question: {question}\nSQL: {sql}\nResults: {json.dumps(preview)}"}
            ],
            temperature=0.3,
            max_tokens=300,
        )
        raw = re.sub(r"```json|```", "", response.choices[0].message.content.strip()).strip()
        data = json.loads(raw)
        if isinstance(data, list):
            return data[:3]
    except Exception as e:
        log.warning(f"[INSIGHTS] Failed: {e}")
    return []


# ── 6. Follow-up suggestions ──────────────────────────────────────

def generate_followups(question: str, sql: str, tables: dict, active_table: str) -> list:
    """3 smart follow-up questions based on current query + schema."""
    schema_str = build_schema_prompt(tables)
    try:
        response = get_client().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content":
                    "You are a data analyst assistant. Suggest exactly 3 smart follow-up questions "
                    "the user might want to ask next. Make them specific, short (under 10 words each), "
                    "and directly related to the data. Return a JSON array of 3 strings. No markdown."},
                {"role": "user", "content":
                    f"User asked: {question}\nSQL: {sql}\nSchema:\n{schema_str}"}
            ],
            temperature=0.5,
            max_tokens=200,
        )
        raw = re.sub(r"```json|```", "", response.choices[0].message.content.strip()).strip()
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(q) for q in data[:3]]
    except Exception as e:
        log.warning(f"[FOLLOWUPS] Failed: {e}")
    return []


# ── 7. Column validation ──────────────────────────────────────────

def validate_sql_columns(sql: str, tables: dict) -> bool:
    all_cols = set()
    for cols in tables.values():
        for c in cols:
            all_cols.add(c.lower())
    bracketed = re.findall(r'\[([^\]]+)\]', sql)
    table_names = set(t.lower() for t in tables.keys())
    for name in bracketed:
        nl = name.lower()
        if nl in table_names or nl in all_cols:
            continue
        log.warning(f"[VALIDATE] Suspicious name in SQL: [{name}]")
        return False
    return True
