"""
llm_sql.py — SQL generation using Groq API (free, fast, Llama3)
Get your free API key at: https://console.groq.com
"""

import os
import re
from groq import Groq

client = None

def get_client():
    global client
    if client is None:
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set.")
        client = Groq(api_key=api_key)
    return client

def build_schema_prompt(tables: dict) -> str:
    """Build a schema description string from {table: [cols]} dict."""
    lines = []
    for table, cols in tables.items():
        lines.append(f"Table: {table}")
        lines.append(f"  Columns: {', '.join(cols)}")
    return "\n".join(lines)

def generate_sql_llm(question: str, tables: dict, active_table: str = None) -> str:
    """
    Use Groq LLM to convert natural language to SQL.
    Injects the real schema so the model knows your actual tables/columns.
    """
    schema_str = build_schema_prompt(tables)
    active_hint = f"\nThe user is currently focused on table: [{active_table}]" if active_table else ""

    system_prompt = f"""You are an expert SQL generator for SQLite databases.
Generate ONLY a valid SQLite SELECT query. No explanation, no markdown, no code blocks. Just raw SQL starting with SELECT.

Strict rules:
- Start with SELECT
- Use only SELECT statements — never DELETE, DROP, UPDATE, INSERT
- Use exact column names from the schema below — never invent column names
- MAX, MIN, SUM, AVG, COUNT are SQL functions — always use them as MAX([col]), MIN([col]), etc.
- Wrap ALL table and column names in square brackets like [table_name] and [column_name]
- End with a semicolon
- If you cannot answer, return: SELECT * FROM [{active_table or list(tables.keys())[0]}] LIMIT 10;

Database Schema:
{schema_str}{active_hint}
"""

    user_prompt = f"Question: {question}\nSQL:"

    response = get_client().chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0,
        max_tokens=200,
    )

    sql = response.choices[0].message.content.strip()

    # Clean up any accidental markdown
    sql = re.sub(r"```sql|```", "", sql).strip()
    # Take only first statement if multiple
    sql = sql.split(";")[0].strip() + ";"
    return sql
