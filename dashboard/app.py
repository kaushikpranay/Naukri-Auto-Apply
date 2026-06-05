"""
FastAPI application for the Naukri Automation Dashboard.

Routes map 1:1 with dashboard pages. All data is read-only from jobs.db.
"""

import math
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from dashboard import db

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Naukri Automation Dashboard", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _pagination(total: int, page: int, per_page: int) -> dict:
    """Build pagination metadata."""
    total_pages = max(1, math.ceil(total / per_page))
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }


def _render(request: Request, template: str, **ctx):
    """Render template with Starlette-compatible signature."""
    return templates.TemplateResponse(request=request, name=template, context=ctx)


# ── Overview ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def overview(request: Request):
    stats = db.get_overview_stats()
    return _render(request, "overview.html", stats=stats, active_page="overview")


# ── Top Jobs ──────────────────────────────────────────────────────────────────

@app.get("/top-jobs", response_class=HTMLResponse)
async def top_jobs(
    request: Request,
    page: int = Query(1, ge=1),
    sort: str = Query("probability"),
    order: str = Query("desc"),
    search: str = Query(""),
):
    jobs, total = db.get_top_jobs(page=page, sort=sort, order=order, search=search)
    pag = _pagination(total, page, 25)
    return _render(request, "top_jobs.html",
                   jobs=jobs, pagination=pag, sort=sort, order=order,
                   search=search, active_page="top_jobs")


# ── External Portal ──────────────────────────────────────────────────────────

@app.get("/external", response_class=HTMLResponse)
async def external_portal(
    request: Request,
    page: int = Query(1, ge=1),
    search: str = Query(""),
):
    jobs, total = db.get_external_portal_jobs(page=page, search=search)
    pag = _pagination(total, page, 25)
    return _render(request, "external.html",
                   jobs=jobs, pagination=pag, search=search, active_page="external")


# ── Review Jobs ──────────────────────────────────────────────────────────────

@app.get("/review", response_class=HTMLResponse)
async def review_jobs(
    request: Request,
    page: int = Query(1, ge=1),
    search: str = Query(""),
):
    jobs, total = db.get_review_jobs(page=page, search=search)
    pag = _pagination(total, page, 25)
    return _render(request, "review.html",
                   jobs=jobs, pagination=pag, search=search, active_page="review")


# ── Failed Jobs ──────────────────────────────────────────────────────────────

@app.get("/failed", response_class=HTMLResponse)
async def failed_jobs(
    request: Request,
    page: int = Query(1, ge=1),
    search: str = Query(""),
):
    jobs, total = db.get_failed_jobs(page=page, search=search)
    pag = _pagination(total, page, 25)
    return _render(request, "failed.html",
                   jobs=jobs, pagination=pag, search=search, active_page="failed")


# ── Question Bank ────────────────────────────────────────────────────────────

@app.get("/questions", response_class=HTMLResponse)
async def question_bank(
    request: Request,
    page: int = Query(1, ge=1),
    search: str = Query(""),
):
    questions, total = db.get_question_bank(page=page, search=search)
    pag = _pagination(total, page, 25)
    return _render(request, "question_bank.html",
                   questions=questions, pagination=pag, search=search,
                   active_page="questions")


# ── System Status ────────────────────────────────────────────────────────────

@app.get("/system", response_class=HTMLResponse)
async def system_status(request: Request):
    status = db.get_system_status()
    return _render(request, "system_status.html",
                   status=status, active_page="system")


# ── JSON API (for auto-refresh) ──────────────────────────────────────────────

@app.get("/api/overview")
async def api_overview():
    return db.get_overview_stats()


@app.get("/api/system")
async def api_system():
    return db.get_system_status()
