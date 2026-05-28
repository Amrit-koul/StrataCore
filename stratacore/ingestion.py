import io
import logging
import re
import pandas as pd

from stratacore.db import drop_all_tables, get_conn

log = logging.getLogger(__name__)


def _sanitize_table_name(filename: str) -> str:
    base = re.sub(r"\.(csv|xlsx|xls)$", "", filename, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9_]", "_", base.lower())


def load_file_to_sqlite(content: bytes, filename: str) -> tuple[str, list[str], list[dict], int]:
    """
    Load CSV or Excel into SQLite (all columns as TEXT for NL→SQL compatibility).
    Replaces existing tables.
    """
    buf = io.BytesIO(content)
    lower = filename.lower()
    if lower.endswith(".csv"):
        df = pd.read_csv(buf, dtype=str, keep_default_na=False)
    elif lower.endswith(".xlsx"):
        df = pd.read_excel(buf, dtype=str, keep_default_na=False, engine="openpyxl")
    elif lower.endswith(".xls"):
        df = pd.read_excel(buf, dtype=str, keep_default_na=False, engine="xlrd")
    else:
        raise ValueError("Unsupported file type. Use .csv, .xlsx, or .xls.")

    if df.empty:
        raise ValueError("File has no rows.")

    df = df.fillna("")
    table_name = _sanitize_table_name(filename)
    cols = [str(c).strip() for c in df.columns]
    df.columns = cols

    drop_all_tables()
    conn = get_conn()
    cur = conn.cursor()
    col_defs = ", ".join(f"[{c}] TEXT" for c in cols)
    cur.execute(f"CREATE TABLE [{table_name}] ({col_defs})")
    placeholders = ", ".join("?" * len(cols))
    for _, row in df.iterrows():
        cur.execute(
            f"INSERT INTO [{table_name}] VALUES ({placeholders})",
            [str(row[c]) for c in cols],
        )
    conn.commit()
    conn.close()

    preview = df.head(5).to_dict(orient="records")
    log.info("[UPLOAD] table=%s rows=%s cols=%s", table_name, len(df), cols)
    return table_name, cols, preview, len(df)
