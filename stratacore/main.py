import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from stratacore.config import llm_available
from stratacore.core_state import CoreState
from stratacore.db import drop_all_tables, get_schema, get_tables
from stratacore.ingestion import load_file_to_sqlite
from stratacore.plotly_figures import build_chart_payload
from stratacore import llm_sql
from stratacore.query_engine import explain_sql, generate_sql
from stratacore.sql_ops import exec_sql, is_safe

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.core = CoreState()
    yield


app = FastAPI(title="StrataCore", lifespan=lifespan)


class QueryBody(BaseModel):
    question: str = Field(..., max_length=500)
    active_table: str | None = None


class ChartBody(BaseModel):
    results: list[dict]
    chart_type: str | None = None


class RunSqlBody(BaseModel):
    sql: str


def _core(request: Request) -> CoreState:
    return request.app.state.core


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.get("/status")
async def status():
    return {"model_ready": True, "llm_active": llm_available()}


@app.get("/metrics")
async def metrics(request: Request):
    core = _core(request)
    m = core.metrics
    total = m["total"] or 1
    return {
        "total_queries": m["total"],
        "success_rate": round(m["success"] / total * 100, 1),
        "llm_used": m["llm_used"],
        "self_corrections": m["self_corrected"],
        "avg_latency_ms": round(sum(m["latencies"]) / len(m["latencies"]) * 1000) if m["latencies"] else 0,
    }


@app.post("/query")
async def query(request: Request, body: QueryBody):
    core = _core(request)
    question = body.question.strip()
    if not question:
        return JSONResponse({"error": "Please enter a question"}, status_code=400)

    core.metrics["total"] += 1
    t_start = time.time()
    sql = None

    try:
        log.info("[QUERY] '%s' | table: '%s'", question, body.active_table)
        sql = generate_sql(question, body.active_table, core)
        log.info("[QUERY] Final SQL: %s", sql)

        if not is_safe(sql):
            return JSONResponse({"error": "Only SELECT queries are allowed"}, status_code=403)

        results = exec_sql(sql)
        latency = time.time() - t_start
        core.metrics["success"] += 1
        core.metrics["latencies"].append(latency)
        if len(core.metrics["latencies"]) > 100:
            core.metrics["latencies"] = core.metrics["latencies"][-100:]

        log.info("[QUERY] OK %s rows | %.0fms", len(results), latency * 1000)

        insights, followups, chart_type = [], [], "bar"
        if llm_available():
            try:
                tables = get_tables(core.active_csv_table)
                insights = llm_sql.generate_insights(question, sql, results)
                followups = llm_sql.generate_followups(question, sql, tables, body.active_table or "")
                chart_type = llm_sql.suggest_chart_type(question, sql, results)
            except Exception as e:
                log.warning("[EXTRAS] Failed: %s", e)

        core.conversation_history.append({"role": "user", "content": question})
        core.conversation_history.append(
            {"role": "assistant", "content": f"Ran: {sql.strip()} → {len(results)} rows returned"}
        )
        if len(core.conversation_history) > 20:
            core.conversation_history = core.conversation_history[-20:]

        intent = llm_sql.classify_intent(question)

        return {
            "sql": sql,
            "results": results,
            "explanation": explain_sql(sql, question),
            "schema": get_schema(core.active_csv_table),
            "count": len(results),
            "insights": insights,
            "followups": followups,
            "chart_type": chart_type,
            "intent": intent,
            "latency_ms": round(latency * 1000),
        }

    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except sqlite3.Error as e:
        log.error("[QUERY] SQLite error: %s | SQL: %s", e, sql)
        return JSONResponse({"error": f"SQL error: {e}", "sql": sql or ""}, status_code=422)
    except Exception as e:
        log.error("[QUERY] Error: %s", e)
        return JSONResponse({"error": f"Error: {e}"}, status_code=500)


@app.post("/upload-table")
async def upload_table(request: Request, file: UploadFile = File(...)):
    core = _core(request)
    name = file.filename or "data.csv"
    lower = name.lower()
    if not lower.endswith((".csv", ".xlsx", ".xls")):
        return JSONResponse(
            {"error": "Unsupported format. Upload .csv, .xlsx, or .xls."},
            status_code=400,
        )
    try:
        content = await file.read()
        table_name, cols, preview, nrows = load_file_to_sqlite(content, name)
        core.active_csv_table = table_name
        return {
            "message": f"Table '{table_name}' created with {nrows} rows",
            "table": table_name,
            "columns": cols,
            "preview": preview,
        }
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        log.error("[UPLOAD] Failed: %s", e)
        return JSONResponse({"error": f"Failed to process file: {e}"}, status_code=500)


@app.post("/clear-db")
async def clear_db(request: Request):
    core = _core(request)
    drop_all_tables()
    core.active_csv_table = None
    core.conversation_history = []
    return {"message": "Database cleared"}


@app.post("/clear-history")
async def clear_history(request: Request):
    _core(request).conversation_history = []
    return {"message": "Conversation history cleared"}


@app.post("/chart-figure")
async def chart_figure(body: ChartBody):
    payload = build_chart_payload(body.results, body.chart_type)
    if not payload:
        return JSONResponse({"error": "No data"}, status_code=400)
    return payload


@app.post("/run-sql")
async def run_sql(body: RunSqlBody):
    sql = body.sql.strip()
    if not sql:
        return JSONResponse({"error": "No SQL provided"}, status_code=400)
    if not is_safe(sql):
        return JSONResponse({"error": "Only SELECT queries are allowed"}, status_code=403)
    try:
        results = exec_sql(sql)
        return {"results": results, "count": len(results)}
    except sqlite3.Error as e:
        return JSONResponse({"error": f"SQL error: {e}"}, status_code=422)


@app.get("/schema")
async def schema_route(request: Request):
    return get_schema(_core(request).active_csv_table)


@app.get("/tables")
async def tables_route(request: Request):
    return get_tables(_core(request).active_csv_table)
