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

Contents
- `app.py`: FastAPI web frontend and API endpoints
- `backend.py`: core agent orchestration / travel-planner logic
- `mcp_client.py`: client helpers to interact with the MCP server
- `custom_weather_mcp_server.py`: example MCP server for weather checks
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

Configuration & environment
- Secrets and API keys are not included in the repo. Set these environment variables in `.env` or the process environment:
	- `GROQ_API_KEY` — required for the LLM agents.
	- `GROQ_MODEL` — optional Groq model name; defaults to `openai/gpt-oss-20b`.
	- `MCP_RETRIES` — optional retry count for live MCP calls; defaults to `2`.
	- `MCP_CACHE_TTL_SECONDS` — optional read-cache lifetime; defaults to `300` seconds.
	- `DATABASE_URL` — required PostgreSQL connection URL for LangGraph checkpoint persistence.
	- MCP/Tavily credentials used by `mcp_client.py` — required for live search and weather data; agents provide fallback guidance when those services are unavailable.

Example requests
- `Plan a 4-day trip to Dubai next month.`
- `Plan a 4-day Dubai trip with budget hotels, vegetarian food, outdoor activities, and a relaxed schedule.`
- `Plan a trip with outdoor activities and check the weather.`
- After the draft appears, request `Move the outdoor activity because I do not want to travel in rain.`

Development notes
- Synchronous LangGraph helpers run in worker threads from FastAPI so the async MCP calls have their own event loop.
- Run the automated checks with `python -m unittest discover -s tests -v`.
- See `DEPLOYMENT.md` for Docker and Render deployment instructions.

Contributing
- Contributions are welcome. Please open issues or pull requests for bug fixes, documentation improvements, or new adapter examples.

License
- This repository follows the license in the `LICENSE` file.

Acknowledgements
- Built as a demonstration of LangGraph + MCP patterns with supervisor and guardrail concepts.

Contact
- For questions or suggestions, open an issue or contact the repository owner.
