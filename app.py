from pathlib import Path
import traceback
import mimetypes
import asyncio
import os
import requests

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from backend import run_travel_agent, resume_travel_agent

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Roamly",
    description=(
        "LangGraph Multi-Agent Travel Planner with Supervisor, Guardrails, "
        "Human-in-the-Loop, and FastAPI Frontend"
    ),
    version="2.0.0",
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
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": f"Location lookup unavailable: {exc}"},
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
        print("ERROR:", exc)
        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc),
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
        print("APPROVAL ERROR:", exc)
        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc),
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
