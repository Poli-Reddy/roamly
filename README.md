# Multi-Agent-System-using-LangGraph-MCP-Supervisor-Guardrails-HITL

A multi-agent travel-planning assistant that uses LangGraph, MCP, a Supervisor, input Guardrails, and Human-In-The-Loop (HITL) approval flows. It produces personalized, safety-aware plans while preserving the original reviewable workflow.

Key ideas:
- Multi-agent coordination using LangGraph and MCP
- Supervisor agent to manage complex workflows
- Input guardrails to validate user requests
- Dynamic flight, hotel, weather, budget, itinerary, personalization, and safety agents
- Shared preferences and safety analysis passed through `TravelState`
- Human-in-the-loop approval for generated plans
- Targeted reruns when review feedback changes preferences or safety constraints
- Optional API-key authentication, request IDs, structured logs, and rate limiting
- Readiness checks for deployment health probes
- A 60-scenario live evaluation dataset and evidence-first metrics runner

Contents
- `app.py`: FastAPI web frontend and API endpoints
- `backend.py`: core agent orchestration / travel-planner logic
- `mcp_client.py`: client helpers to interact with the MCP server
- `custom_weather_mcp_server.py`: example MCP server for weather checks
- `evaluation/dataset.json`: labeled evaluation scenarios across 10 travel categories
- `evaluation/routing_dataset.json`: 50 scenarios with expected specialist-agent sets
- `evaluation/quality_dataset.json`: labeled constraint and tool-evidence cases
- `evaluation/runner.py`: live runner that records observed outcomes without fabricated scores
- `evaluation/routing_runner.py`: exact selected-agent-set accuracy evaluator
- `evaluation/load_test.py`: concurrent HTTP latency and success probe
- `evaluation/quality.py`: explicitly heuristic constraint/evidence coverage checks
- `evaluation/mcp_runner.py`: real MCP-only execution evaluator
- `evaluation/hitl_runner.py`: real PostgreSQL-backed interrupt/resume evaluator
- `evaluation/security_runner.py`: live application guardrail/security evaluator
- `evaluation/validate_datasets.py`: schema and label validation without providers
- `templates/`, `static/`: frontend UI assets (HTML, JS, CSS)

Features
- Interactive web UI for sending travel planning prompts
- Endpoint for drafting travel plans and separate approval endpoint
- Personalization panel showing detected traveler preferences
- Safety panel showing risk level, warnings, and recommendations
- Example MCP server demonstrating domain adapters (weather, checkpoints)

Architecture
- `supervisor_agent` validates the request and dynamically selects only the relevant specialist agents.
- `personalization_agent` extracts explicit traveler preferences into `user_preferences`.
- `safety_agent` evaluates existing weather and itinerary output and stores `safety_analysis`.
- The itinerary and final-response agents consume both shared-state fields.
- The browser can optionally provide a permission-based starting location. Roamly reverse-geocodes it to a city/country and passes the trip as `origin -> destination`; users can enter an origin manually or continue without sharing location.
- HITL pauses after the draft. Approval proceeds to the final response; preference or safety-related revision feedback reruns only relevant specialists and itinerary work.

Prerequisites
- Python 3.10+ (recommended)
- Git (to clone the repo)
- A virtual environment tool (venv) or similar

Quick start (Windows)

1. Create and activate a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1    # PowerShell
```

2. Install dependencies

```powershell
pip install -r requirements.txt
```

3. Run the FastAPI app

```powershell
# option A (module entry point)
python app.py

# option B (production-like local startup)
uvicorn app:app --host 0.0.0.0 --port 8000
```

4. Open the web UI

Visit http://127.0.0.1:8000 in your browser to use the Roamly frontend.

Running the MCP server (example)
- The repository includes `custom_weather_mcp_server.py` as an example MCP server. Run it in a separate terminal if you want to experiment with custom adapters used by the demo.

```powershell
# start example MCP server (if needed)
python custom_weather_mcp_server.py
```

API Endpoints
- `POST /api/travel` — create or resume a travel planning thread. JSON: `{ "message": "<user prompt>", "thread_id": "optional-thread-id" }`
- `POST /api/travel/approve` — approve or request revisions for a draft. JSON: `{ "thread_id": "<id>", "approved": true|false, "feedback": "optional" }`
- `GET /api/location/reverse` — reverse-geocode browser coordinates after the user grants location permission.
- `GET /health` — basic health check and features list
- `GET /ready` — readiness check for required LLM and PostgreSQL configuration

Production controls
- Set `ROAMLY_API_KEY` to require `X-API-Key` or `Authorization: Bearer ...` on `/api/*` requests.
- `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW_SECONDS` provide a bounded per-client in-process limit.
- Every response includes `X-Request-ID`; request completion logs include status and latency.
- `/health` is liveness. `/ready` is readiness and returns 503 when required configuration is absent.

Configuration & environment
- Secrets and API keys are not included in the repo. Set these environment variables in `.env` or the process environment:
	- `GROQ_API_KEY` — required for the LLM agents.
	- `GROQ_MODEL` — optional Groq model name; defaults to `openai/gpt-oss-20b`.
	- `MCP_RETRIES` — optional retry count for live MCP calls; defaults to `2`.
	- `MCP_CACHE_TTL_SECONDS` — optional read-cache lifetime; defaults to `300` seconds.
	- `ROAMLY_API_KEY` — optional API key for production API authentication.
	- `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` — optional API rate-limit window.
	- `LOG_LEVEL` — optional logging level; defaults to `INFO`.
	- `DATABASE_URL` — required PostgreSQL connection URL for LangGraph checkpoint persistence.
	- MCP/Tavily credentials used by `mcp_client.py` — required for live search and weather data; agents provide fallback guidance when those services are unavailable.

Example requests
- `Plan a 4-day trip to Dubai next month.`
- `Plan a 4-day Dubai trip with budget hotels, vegetarian food, outdoor activities, and a relaxed schedule.`
- `Plan a trip with outdoor activities and check the weather.`
- After the draft appears, request `Move the outdoor activity because I do not want to travel in rain.`

Development notes
- Synchronous LangGraph helpers run in worker threads from FastAPI so the async MCP calls have their own event loop.
- **Level 1 unit/regression:** `python -m unittest discover -s tests -v` (mocks are intentional and do not prove live AI behavior).
- **Dataset contract checks:** `python evaluation/validate_datasets.py` (no provider calls).
- Validate the evaluation dataset with `python evaluation/runner.py --help`.
- Run a real evaluation only after configuring live credentials and PostgreSQL:

```powershell
python evaluation/runner.py --mode live --output evaluation/results/live-run.json
```

The runner executes the labeled scenarios against the configured system and stores
per-case latency, task completion, structured-output, guardrail, and MCP metrics.
It reports provider cost and semantic hallucination rate as unknown/not claimed
when the configured provider does not expose enough evidence. It never fills in
invented numbers. Evaluation result files are ignored by git.

Additional live evaluations:

```powershell
python evaluation/routing_runner.py --output evaluation/results/routing-run.json
python evaluation/runner.py --dataset evaluation/quality_dataset.json --output evaluation/results/quality-run.json
python evaluation/load_test.py --requests 10 --concurrency 5
python evaluation/mcp_runner.py --output evaluation/results/mcp-run.json
python evaluation/hitl_runner.py --output evaluation/results/hitl-run.json
python evaluation/security_runner.py --output evaluation/results/security-run.json
```

Routing accuracy is an exact match against the labeled agent set. Quality scores
are case-insensitive term-coverage heuristics and are not a substitute for human
factuality review. Prompt-injection cases are blocked deterministically before
the LLM guardrail and covered by regression tests.

Live commands require real credentials and services. They fail clearly when
configuration is missing and never switch to mock mode. Evaluation artifacts
include a run ID, dataset hash, Git SHA when available, model, environment, and
per-case evidence. Existing result files are never overwritten.
- See `DEPLOYMENT.md` for Docker and Render deployment instructions.

Contributing
- Contributions are welcome. Please open issues or pull requests for bug fixes, documentation improvements, or new adapter examples.

License
- This repository follows the license in the `LICENSE` file.

Acknowledgements
- Built as a demonstration of LangGraph + MCP patterns with supervisor and guardrail concepts.

Contact
- For questions or suggestions, open an issue or contact the repository owner.
