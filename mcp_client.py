import os
import shutil
import sys
import asyncio
import time
import re
import random
import logging
from pathlib import Path
from typing import Any

import certifi
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_mcp_adapters.client import MultiServerMCPClient


# =========================================================
# Environment setup
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Support both environment-variable names.
AVIATION_STACK_API_KEY = (
    os.getenv("AVIATION_STACK_API_KEY")
    or os.getenv("AVIATIONSTACK_API_KEY")
)

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
MCP_RETRIES = max(0, int(os.getenv("MCP_RETRIES", "2")))
MCP_CACHE_TTL_SECONDS = max(0, int(os.getenv("MCP_CACHE_TTL_SECONDS", "300")))

ALLOWED_MCP_TOOLS = {
    "tavily": {"tavily_search"},
    "aviationstack": {"list_airports", "list_airlines"},
    "weather": {"get_current_weather", "get_forecast"},
}

WEATHER_SERVER_PATH = BASE_DIR / "custom_weather_mcp_server.py"
UVX_COMMAND = os.getenv("UVX_COMMAND") or shutil.which("uvx") or "uvx"
logger = logging.getLogger("roamly.mcp")


def _require_env(name: str, value: str | None) -> str:
    """Return an environment value or raise a readable setup error."""

    if not value:
        raise RuntimeError(
            f"{name} is missing. "
            f"Add {name}=your_key to the project .env file."
        )

    return value


def _subprocess_env(**updates: str | None) -> dict[str, str]:
    """
    Preserve the current Windows/Conda environment and add MCP API keys.
    """

    env = os.environ.copy()

    for key, value in updates.items():
        if value:
            env[key] = value

    return env


# =========================================================
# LLM
# =========================================================

llm = ChatGroq(
    model=GROQ_MODEL,
    api_key=_require_env("GROQ_API_KEY", GROQ_API_KEY),
)


# =========================================================
# MCP client
# =========================================================

client = MultiServerMCPClient(
    {
        "tavily": {
            "transport": "streamable_http",
            "url": (
                "https://mcp.tavily.com/mcp/"
                f"?tavilyApiKey={TAVILY_API_KEY or ''}"
            ),
        },

        "aviationstack": {
            "transport": "stdio",
            "command": UVX_COMMAND,
            "args": [
                "aviationstack-mcp",
            ],
            "env": _subprocess_env(
                AVIATION_STACK_API_KEY=AVIATION_STACK_API_KEY,
            ),
        },

        "weather": {
            "transport": "stdio",

            # Uses the Python executable from the active Conda environment.
            "command": sys.executable,

            # Uses the weather server inside the current project folder.
            "args": [
                str(WEATHER_SERVER_PATH),
            ],

            "env": _subprocess_env(
                OPENWEATHER_API_KEY=OPENWEATHER_API_KEY,
            ),
        },
    }
)

_tool_cache: dict[tuple[str, str, str], tuple[float, Any]] = {}
_tool_metrics = {
    "calls": 0,
    "successes": 0,
    "failures": 0,
    "cache_hits": 0,
}
_tool_events: list[dict[str, Any]] = []


def _safe_error(exc: Exception) -> str:
    """Remove common credential-shaped query values from telemetry."""
    message = str(exc)
    return re.sub(r"(?i)(api[_-]?key|token|secret|password)=[^&\s]+", r"\1=[redacted]", message)[:500]


def _evidence_snapshot(result: Any) -> str:
    """Keep returned tool evidence bounded without logging credentials."""
    text = repr(result)
    return text[:4000]


def _tool_result_error(result: Any) -> str | None:
    """Detect MCP adapters that encode tool failures as text content."""
    text = _evidence_snapshot(result)
    match = re.search(r"Error executing tool[^\n]*", text, flags=re.IGNORECASE)
    return match.group(0)[:500] if match else None


def _error_category(exc: Exception) -> str:
    message = str(exc).lower()
    if isinstance(exc, (ValueError, TypeError)) or "invalid" in message or "empty" in message:
        return "VALIDATION_FAILURE"
    if "api key" in message or "unauthorized" in message or "forbidden" in message:
        return "AUTHENTICATION_FAILURE"
    if "uvx was not found" in message or "not found" in message:
        return "DEPENDENCY_UNAVAILABLE"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timeout" in message:
        return "TIMEOUT"
    if "429" in message or "rate limit" in message:
        return "RATE_LIMITED"
    if "tool" in message and "not found" in message:
        return "TOOL_NOT_FOUND"
    if (
        isinstance(exc, (ConnectionError, OSError))
        or "connection" in message
        or "network" in message
        or "temporary" in message
        or "outage" in message
        or "offline" in message
    ):
        return "NETWORK_ERROR"
    if "500" in message or "502" in message or "503" in message:
        return "PROVIDER_ERROR"
    return "UNEXPECTED_ERROR"


def _is_retryable(exc: Exception) -> bool:
    return _error_category(exc) in {
        "TIMEOUT",
        "RATE_LIMITED",
        "NETWORK_ERROR",
        "PROVIDER_ERROR",
    }


def reset_tool_metrics() -> None:
    _tool_events.clear()
    for metric in _tool_metrics:
        _tool_metrics[metric] = 0


def get_tool_metrics() -> dict[str, Any]:
    return {**_tool_metrics, "events": list(_tool_events)}


async def _get_server_tool(
    server_name: str,
    tool_name: str,
):
    """
    Load one tool from one MCP server.

    This prevents a broken weather or AviationStack server from
    crashing an unrelated Tavily request.
    """

    if server_name == "tavily":
        _require_env(
            "TAVILY_API_KEY",
            TAVILY_API_KEY,
        )

    elif server_name == "aviationstack":
        _require_env(
            "AVIATION_STACK_API_KEY",
            AVIATION_STACK_API_KEY,
        )

        if shutil.which("uvx") is None:
            raise RuntimeError(
                "uvx was not found. Install uv, reopen the terminal, "
                "activate the travel environment, and run "
                "`uvx --version`."
            )

    elif server_name == "weather":
        _require_env(
            "OPENWEATHER_API_KEY",
            OPENWEATHER_API_KEY,
        )

        if not WEATHER_SERVER_PATH.is_file():
            raise FileNotFoundError(
                f"Weather MCP server not found: "
                f"{WEATHER_SERVER_PATH}"
            )

    # Important: load only the requested MCP server.
    tools = await client.get_tools(
        server_name=server_name,
    )

    tool = next(
        (
            item
            for item in tools
            if item.name == tool_name
        ),
        None,
    )

    if tool is None:
        available_tools = (
            ", ".join(
                sorted(item.name for item in tools)
            )
            or "none"
        )

        raise RuntimeError(
            f"MCP tool '{tool_name}' was not found "
            f"on server '{server_name}'. "
            f"Available tools: {available_tools}"
        )

    return tool


async def _invoke_tool(
    server_name: str,
    tool_name: str,
    tool_args: dict[str, Any],
    cache_key: str,
):
    """Invoke a read-only MCP tool with bounded retries and short-lived caching."""
    allowed_tools = ALLOWED_MCP_TOOLS.get(server_name, set())
    if tool_name not in allowed_tools:
        raise ValueError(f"MCP tool is not allowlisted: {server_name}/{tool_name}")
    if server_name in {"tavily", "weather"}:
        expected_key = "query" if server_name == "tavily" else "city"
        if not isinstance(tool_args.get(expected_key), str) or not tool_args[expected_key].strip():
            raise TypeError(f"MCP argument '{expected_key}' must be a string")
    elif not isinstance(tool_args, dict):
        raise TypeError("MCP tool arguments must be an object")

    key = (server_name, tool_name, cache_key)
    now = time.monotonic()
    cached = _tool_cache.get(key)
    if cached and now - cached[0] < MCP_CACHE_TTL_SECONDS:
        _tool_metrics["cache_hits"] += 1
        _tool_events.append(
            {
                "server": server_name,
                "tool": tool_name,
                "cache": "hit",
                "success": True,
                "latency_ms": round((time.monotonic() - now) * 1000, 2),
                "attempt": 0,
                "evidence": _evidence_snapshot(cached[1]),
            }
        )
        return cached[1]

    last_error: Exception | None = None
    for attempt in range(MCP_RETRIES + 1):
        _tool_metrics["calls"] += 1
        attempt_started = time.monotonic()
        try:
            tool = await _get_server_tool(server_name, tool_name)
            result = await tool.ainvoke(tool_args)
            tool_error = _tool_result_error(result)
            if tool_error:
                raise RuntimeError(tool_error)
            _tool_metrics["successes"] += 1
            _tool_events.append(
                {
                    "server": server_name,
                    "tool": tool_name,
                    "cache": "miss",
                    "success": True,
                    "latency_ms": round((time.monotonic() - attempt_started) * 1000, 2),
                    "attempt": attempt + 1,
                    "evidence": _evidence_snapshot(result),
                }
            )
            if MCP_CACHE_TTL_SECONDS:
                _tool_cache[key] = (time.monotonic(), result)
            return result
        except Exception as exc:
            last_error = exc
            _tool_events.append(
                {
                    "server": server_name,
                    "tool": tool_name,
                    "cache": "miss",
                    "success": False,
                    "latency_ms": round((time.monotonic() - attempt_started) * 1000, 2),
                    "attempt": attempt + 1,
                    "exception_type": type(exc).__name__,
                    "error_category": _error_category(exc),
                    "error": _safe_error(exc),
                }
            )
            if attempt < MCP_RETRIES and _is_retryable(exc):
                delay = min(4.0, 0.25 * (2**attempt)) + random.uniform(0, 0.25)
                await asyncio.sleep(delay)
            elif not _is_retryable(exc):
                _tool_metrics["failures"] += 1
                break
            elif attempt == MCP_RETRIES:
                _tool_metrics["failures"] += 1

    raise RuntimeError(
        f"MCP {server_name}/{tool_name} failed after {MCP_RETRIES + 1} attempts: "
        f"{last_error}"
    ) from last_error


# =========================================================
# MCP connection test
# =========================================================

async def get_all_tools() -> None:
    """
    Test every MCP server independently.

    One failed server will not stop the remaining tests.
    """

    for server_name in (
        "tavily",
        "aviationstack",
        "weather",
    ):
        try:
            tools = await client.get_tools(
                server_name=server_name,
            )

            tool_names = (
                ", ".join(
                    tool.name
                    for tool in tools
                )
                or "no tools"
            )

            logger.info("mcp_server_tools server=%s tools=%s", server_name, tool_names)

        except Exception as exc:
            logger.error(
                "mcp_server_tools_failed server=%s error=%s",
                server_name,
                _safe_error(exc),
            )


# =========================================================
# Tavily MCP
# =========================================================

async def tavily_mcp_search(query: str):
    return await _invoke_tool(
        "tavily",
        "tavily_search",
        {"query": query},
        query.strip().lower(),
    )


# =========================================================
# AviationStack MCP
# =========================================================

async def aviation_mcp_call(
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
):
    args = tool_args or {}
    return await _invoke_tool(
        "aviationstack",
        tool_name,
        args,
        repr(sorted(args.items())),
    )


# =========================================================
# Weather MCP
# =========================================================

async def weather_mcp_search(city: str):
    return await _invoke_tool(
        "weather",
        "get_current_weather",
        {"city": city},
        city.strip().lower(),
    )


async def forecast_mcp_search(city: str):
    return await _invoke_tool(
        "weather",
        "get_forecast",
        {"city": city},
        city.strip().lower(),
    )


# =========================================================
# Destination extractor
# =========================================================

def extract_destination(query: str) -> str:
    prompt = f"""
Extract only the destination city or country from the travel request.

Travel request:
{query}

Return only the destination name.
Do not add any explanation.
"""

    response = llm.invoke(prompt)

    destination = str(
        response.content
    ).strip()

    if not destination:
        raise ValueError(
            "The destination could not be extracted."
        )

    return destination