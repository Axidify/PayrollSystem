"""FastAPI entry point for the payroll application."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi import Response

from app.database import init_db
from app.routers import admin, auth, dashboard, models, profile, schedules
from app.database import SessionLocal
from app.services import PayrollService
from datetime import datetime, timedelta
import threading
import time
from pathlib import Path

app = FastAPI(title="Payroll Desk", version="1.0.0")

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


def _seconds_until_next_run(hour: int = 0, minute: int = 5) -> int:
    """Return seconds until next 1st of the month at given hour/minute."""
    now = datetime.now()
    # compute next 1st
    if now.day == 1 and (now.hour < hour or (now.hour == hour and now.minute < minute)):
        target = datetime(year=now.year, month=now.month, day=1, hour=hour, minute=minute, second=0)
    else:
        # advance month
        year = now.year + (1 if now.month == 12 else 0)
        month = 1 if now.month == 12 else now.month + 1
        target = datetime(year=year, month=month, day=1, hour=hour, minute=minute, second=0)
    delta = target - now
    return max(int(delta.total_seconds()), 0)


def _monthly_payroll_worker(stop_event: threading.Event) -> None:
    """Background worker that runs payroll on the 1st of each month at 00:05.

    This is intentionally minimal to avoid adding external scheduler dependencies.
    """
    # Use exports directory at project root
    exports_dir = Path("exports")
    exports_dir.mkdir(exist_ok=True)

    while not stop_event.is_set():
        wait_seconds = _seconds_until_next_run(0, 5)
        # Sleep with checks so the worker can be stopped promptly
        waited = 0
        while waited < wait_seconds and not stop_event.is_set():
            time.sleep(min(30, wait_seconds - waited))
            waited += min(30, wait_seconds - waited)

        if stop_event.is_set():
            break

        # It's time to run payroll for the upcoming month (target = current month)
        try:
            with SessionLocal() as db:
                service = PayrollService(db)
                now = datetime.now()
                # Determine target month/year for the run: the month that just started
                target_year = now.year
                target_month = now.month
                # Default options: include inactive False, currency USD
                service.run_payroll(
                    target_year=target_year,
                    target_month=target_month,
                    currency="USD",
                    include_inactive=False,
                    output_dir=exports_dir,
                )
                print(f"[monthly_worker] Generated payroll for {target_year}-{target_month:02d}")
        except Exception as e:
            print(f"[monthly_worker] Error running payroll: {type(e).__name__}: {e}")


# Start worker thread
_monthly_worker_stop = threading.Event()
_monthly_worker_thread = threading.Thread(target=_monthly_payroll_worker, args=(_monthly_worker_stop,), daemon=True)
_monthly_worker_thread.start()


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/login")


@app.get("/health")
def health() -> Response:
    """Simple health endpoint for load balancers and platform checks."""
    return Response(content='{"status":"ok"}', media_type="application/json")
