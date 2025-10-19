"""FastAPI entry point for the payroll application."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi import Response

from app.database import init_db
from app.routers import admin, auth, dashboard, models, profile, schedules

app = FastAPI(title="Payroll Scheduler", version="1.0.0")

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(admin.router)
app.include_router(dashboard.router)
app.include_router(models.router)
app.include_router(schedules.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/login")


@app.get("/health")
def health() -> Response:
    """Simple health endpoint for load balancers and platform checks."""
    return Response(content='{"status":"ok"}', media_type="application/json")
