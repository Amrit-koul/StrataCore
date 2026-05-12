import sqlite3

from stratacore.config import get_database_path


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_database_path())
    conn.row_factory = sqlite3.Row
    return conn


def drop_all_tables() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    for (t,) in cur.fetchall():
        cur.execute(f"DROP TABLE IF EXISTS [{t}]")
    conn.commit()
    conn.close()


def get_tables(active_table: str | None) -> dict[str, list[str]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = [r[0] for r in cur.fetchall()]
    tables: dict[str, list[str]] = {}
    show = [active_table] if active_table and active_table in all_tables else all_tables
    for t in show:
        cur.execute(f"PRAGMA table_info([{t}])")
        tables[t] = [row[1] for row in cur.fetchall()]
    conn.close()
    return tables


def get_sample_rows(table: str, n: int = 5) -> list[dict]:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM [{table}] LIMIT {n}")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def get_schema(active_table: str | None) -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = [r[0] for r in cur.fetchall()]
    tables = [active_table] if active_table and active_table in all_tables else all_tables
    nodes, edges = [], []
    colors = ["#6366f1", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#06b6d4"]
    for i, t in enumerate(tables):
        cur.execute(f"PRAGMA table_info([{t}])")
        fields = [c[1] + (" (PK)" if c[5] else "") for c in cur.fetchall()]
        nodes.append({"id": t, "label": t, "color": colors[i % len(colors)], "fields": fields})
        cur.execute(f"PRAGMA foreign_key_list([{t}])")
        for fk in cur.fetchall():
            edges.append({"from": fk[2], "to": t, "label": f"{fk[4]} → {fk[3]}"})
    conn.close()
    return {"nodes": nodes, "edges": edges}
