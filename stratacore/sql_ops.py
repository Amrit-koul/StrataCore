import logging
import re
import sqlite3

from stratacore.db import get_conn

log = logging.getLogger(__name__)


def is_valid_sql(sql: str) -> bool:
    if not sql or len(sql) < 6:
        return False
    cleaned = sql.strip().upper()
    if not cleaned.startswith("SELECT"):
        return False
    if "FROM" not in cleaned:
        return False
    if cleaned.count("GROUP BY") > 3 or cleaned.count("ORDER BY") > 3:
        return False
    if re.search(r"\bSELECT\s+(MAX|MIN|SUM|AVG|COUNT)\s+FROM\b", sql, re.IGNORECASE):
        return False
    if "[" not in sql:
        log.warning("[VALIDATE] SQL missing bracket-wrapped names, rejecting: %s", sql)
        return False
    return True


def fix_sql(sql: str) -> str:
    sql = sql.strip().rstrip(";")
    sql = re.sub(r"\s+", " ", sql)
    if re.match(r"^ELECT\b", sql, re.IGNORECASE):
        sql = "S" + sql
    return sql + ";"


def is_safe(sql: str) -> bool:
    dangerous = [
        "DELETE",
        "DROP",
        "UPDATE",
        "INSERT",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "EXEC",
        "PRAGMA",
    ]
    upper = sql.upper()
    return not any(word in upper for word in dangerous)


def exec_sql(sql: str) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def dry_run_sql(sql: str) -> None:
    """Execute SELECT to validate; raises sqlite3.Error on failure."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql)
    cur.fetchall()
    conn.close()
