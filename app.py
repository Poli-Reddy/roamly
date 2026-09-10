from pathlib import Path
import mimetypes
import asyncio
import os
import requests
import logging
import re
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from backend import run_travel_agent, resume_travel_agent

BASE_DIR = Path(__file__).resolve().parent

logger = logging.getLogger("roamly.api")


def _safe_error(exc: Exception) -> str:
    return re.sub(
        r"(?i)(api[_-]?key|token|secret|password)=[^&\s]+",
        r"\1=[redacted]",
        str(exc),
    )[:500]


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s",
)
for handler in logging.getLogger().handlers:
    handler.addFilter(_RequestIdFilter())

API_KEY = os.getenv("ROAMLY_API_KEY", "").strip()
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
if ENVIRONMENT == "production" and not API_KEY:
    raise RuntimeError("ROAMLY_API_KEY is required when ENVIRONMENT=production")
RATE_LIMIT_REQUESTS = max(1, int(os.getenv("RATE_LIMIT_REQUESTS", "30")))
RATE_LIMIT_WINDOW_SECONDS = max(1, int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")))
_request_timestamps: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = asyncio.Lock()


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",", 1)[0].strip() or (request.client.host if request.client else "unknown")


async def _within_rate_limit(key: str) -> bool:
    now = time.monotonic()
    async with _rate_limit_lock:
        timestamps = _request_timestamps[key]
        while timestamps and now - timestamps[0] >= RATE_LIMIT_WINDOW_SECONDS:
            timestamps.popleft()
        if len(timestamps) >= RATE_LIMIT_REQUESTS:
            return False
        timestamps.append(now)
        return True


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("service_started", extra={"request_id": "startup"})
    yield
    logger.info("service_stopped", extra={"request_id": "shutdown"})

app = FastAPI(
    title="Roamly",
    description=(
        "LangGraph Multi-Agent Travel Planner with Supervisor, Guardrails, "
        "Human-in-the-Loop, and FastAPI Frontend"
    ),
    version="2.0.0",
    lifespan=lifespan,
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class TravelRequest(BaseModel):
    message: str = Field(max_length=1200)
    thread_id: str | None = Field(default=None, max_length=128)
    origin_location: str | None = Field(default=None, max_length=160)


class ApprovalRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    approved: bool
    feedback: str = Field(default="", max_length=2000)


RUN_SEMAPHORE = asyncio.Semaphore(2)


@app.middleware("http")
async def production_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()

    if API_KEY and request.url.path.startswith("/api/"):
        supplied_key = request.headers.get("x-api-key", "")
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            supplied_key = authorization[7:].strip()
        if not secrets.compare_digest(supplied_key, API_KEY):
            logger.warning("authentication_failed", extra={"request_id": request_id})
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "Authentication required."},
                headers={"x-request-id": request_id},
            )

    if request.url.path.startswith("/api/") and not await _within_rate_limit(_client_key(request)):
        logger.warning("rate_limit_exceeded", extra={"request_id": request_id})
        return JSONResponse(
            status_code=429,
            content={"success": False, "error": "Rate limit exceeded. Retry later."},
            headers={"retry-after": str(RATE_LIMIT_WINDOW_SECONDS), "x-request-id": request_id},
        )

    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled_request_error", extra={"request_id": request_id})
        response = JSONResponse(
            status_code=500,
            content={"success": False, "error": "Internal server error."},
        )

    response.headers["x-request-id"] = request_id
    response.headers["x-content-type-options"] = "nosniff"
    logger.info(
        "request_complete method=%s path=%s status=%s duration_ms=%.2f",
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
        extra={"request_id": request_id},
    )
    return response


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.get("/static/{file_path:path}")
async def static_asset(file_path: str):
    """Serve frontend assets without Starlette's thread-based directory check."""
    asset_root = (BASE_DIR / "static").resolve()
    asset_path = (asset_root / file_path).resolve()

    if asset_root not in asset_path.parents or not asset_path.is_file():
        return JSONResponse(status_code=404, content={"detail": "Asset not found"})

    media_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
    return Response(content=asset_path.read_bytes(), media_type=media_type)


@app.get("/api/location/reverse")
async def reverse_location(latitude: float, longitude: float):
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid coordinates."})

    try:
        result = await asyncio.to_thread(
            requests.get,
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "lat": latitude,
                "lon": longitude,
                "format": "jsonv2",
                "zoom": 10,
            },
            headers={"User-Agent": "Roamly travel planner/1.0"},
            timeout=8,
        )
        result.raise_for_status()
        address = result.json().get("address", {})
        location = ", ".join(
            part for part in (
                address.get("city")
                or address.get("town")
                or address.get("village"),
                address.get("country"),
            ) if part
        )
        if not location:
            raise ValueError("No readable location was returned.")
        return {"success": True, "location": location}
    except Exception as exc:
        logger.error(
            "reverse_location_failed error=%s",
            _safe_error(exc),
            extra={"request_id": "location"},
        )
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": "Location lookup unavailable."},
        )


@app.post("/api/travel")
async def travel_planner(request_data: TravelRequest):
    try:
        user_message = request_data.message.strip()

        if not user_message:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Message cannot be empty.",
                },
            )

        async with RUN_SEMAPHORE:
            result = await asyncio.to_thread(
                run_travel_agent,
                user_input=user_message,
                thread_id=request_data.thread_id,
                origin_location=request_data.origin_location,
            )

        return JSONResponse(
            content={
                "success": True,
                **result,
            }
        )

    except Exception as exc:
        logger.error(
            "travel_request_failed error=%s",
            _safe_error(exc),
            extra={"request_id": "travel"},
        )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Internal server error.",
            },
        )


@app.post("/api/travel/approve")
async def approve_travel_plan(request_data: ApprovalRequest):
    try:
        if not request_data.approved and not request_data.feedback.strip():
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Please provide revision feedback when rejecting the draft.",
                },
            )

        async with RUN_SEMAPHORE:
            result = await asyncio.to_thread(
                resume_travel_agent,
                thread_id=request_data.thread_id,
                approved=request_data.approved,
                feedback=request_data.feedback,
            )

        return JSONResponse(
            content={
                "success": True,
                **result,
            }
        )

    except Exception as exc:
        logger.error(
            "approval_request_failed error=%s",
            _safe_error(exc),
            extra={"request_id": "approval"},
        )

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Internal server error.",
            },
        )


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "message": "Roamly API is running",
        "features": [
            "supervisor_agent",
            "input_guardrail",
            "personalization_agent",
            "safety_agent",
            "human_in_the_loop",
        ],
    }


@app.get("/ready")
async def readiness_check():
    """Report whether required startup dependencies are configured."""
    configured = {
        "database": bool(os.getenv("DATABASE_URL")),
        "llm": bool(os.getenv("GROQ_API_KEY")),
        "api_auth": bool(API_KEY),
    }
    ready = configured["database"] and configured["llm"]
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": configured},
    )


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "false").lower() == "true",
    )
