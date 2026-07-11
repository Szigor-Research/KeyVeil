from __future__ import annotations

import hashlib
import os
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from agent_wallet import InMemoryBudgetStore, evaluate_payment

from .scenarios import build_scenario, list_scenario_meta

STATIC_DIR = Path(__file__).resolve().parent / "static"


class SimulateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")


app = FastAPI(
    title="KeyVeil Policy Reference",
    version="0.3.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "mode": "synthetic-reference", "version": "0.3.0"})


@app.get("/api/scenarios")
async def api_scenarios() -> JSONResponse:
    return JSONResponse({"scenarios": list_scenario_meta()})


@app.post("/api/simulate")
async def api_simulate(body: SimulateBody) -> JSONResponse:
    try:
        scenario = build_scenario(body.scenario)
    except KeyError:
        return JSONResponse(
            {"ok": False, "error": "unknown synthetic scenario"},
            status_code=404,
        )

    now = int(time.time())
    budget_store = InMemoryBudgetStore()
    for index, amount in enumerate(scenario.committed_today_usd):
        preload = budget_store.reserve(
            session_id=scenario.scope.session_id,
            budget_scope_id=scenario.engine.budget_scope_id,
            intent_id=f"preloaded_{index}",
            intent_hash=hashlib.sha256(f"preloaded_{index}".encode()).hexdigest(),
            amount_usd=amount,
            daily_limit_usd=scenario.scope.daily_budget_usd,
            weekly_limit_usd=scenario.engine.weekly_budget_usd,
            now_epoch=now,
        )
        if preload.reservation is not None:
            budget_store.commit(preload.reservation.reservation_id)

    receipt = evaluate_payment(
        scenario.scope,
        scenario.engine,
        scenario.intent,
        budget_store=budget_store,
        approval=scenario.approval,
        approval_verifier=scenario.approval_authority,
        now_epoch=now,
    )
    payload = asdict(receipt)
    return JSONResponse(
        {
            "ok": True,
            "mode": "synthetic-reference",
            "scenario": {
                "id": scenario.scenario_id,
                "title": scenario.title,
                "expected_status": scenario.expected_status,
            },
            "trace": list(scenario.log_lines),
            "receipt": payload,
        }
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:
    import uvicorn

    host = os.environ.get("KEYVEIL_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT") or os.environ.get("KEYVEIL_PORT", "8765"))
    uvicorn.run("demo.web_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
