# Roamly Deployment

## 1. Application architecture

- Entry point: `app.py`
- Framework: FastAPI served by Uvicorn
- Graph: `backend.py` builds a LangGraph `StateGraph`, compiles it with `PostgresSaver`, and initializes it during import.
- Persistence: PostgreSQL is required for LangGraph checkpoints, HITL pauses, and thread state.
- MCP services:
  - Tavily: remote streamable HTTP MCP service.
  - Weather: local `custom_weather_mcp_server.py` launched as a Python stdio subprocess.
  - AviationStack: `aviationstack-mcp` launched through `uvx` as a stdio subprocess.

## 2. Local setup

Requirements:

- Python 3.11+ recommended
- PostgreSQL database reachable from the machine
- `uv` installed and available as `uvx` if live AviationStack searches are needed

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `.env` with real values. Never commit `.env` or paste credentials into source files.

## 3. Required environment variables

Required:

- `GROQ_API_KEY`
- `DATABASE_URL`
- `TAVILY_API_KEY`
- `AVIATION_STACK_API_KEY`
- `OPENWEATHER_API_KEY`

Recommended:

- `GROQ_MODEL`, default `openai/gpt-oss-20b`
- `MCP_RETRIES`, default `2`
- `MCP_CACHE_TTL_SECONDS`, default `300`
- `ROAMLY_API_KEY`, recommended for any public deployment
- `RATE_LIMIT_REQUESTS`, default `30`
- `RATE_LIMIT_WINDOW_SECONDS`, default `60`
- `LOG_LEVEL`, default `INFO`

Optional LangSmith tracing:

- `LANGCHAIN_TRACING`
- `LANGCHAIN_ENDPOINT`
- `LANGCHAIN_API_KEY`
- `LANGCHAIN_PROJECT`

The platform supplies `PORT` in production. Local startup defaults to port `8000`.

## 4. Run locally

Production-like local command:

```powershell
$env:HOST="0.0.0.0"
$env:PORT="8000"
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Or use the module entry point:

```powershell
python app.py
```

The module entry point reads `HOST`, `PORT`, and `RELOAD` from the environment.

Run tests:

```powershell
python -m unittest discover -s tests -v
```

## 5. Deploy on Render

The repository includes `render.yaml` and a production Dockerfile.

1. Push the repository to GitHub.
2. In Render, choose **New > Blueprint** and select the repository.
3. Render reads `render.yaml` and builds the Docker service.
4. Set the `sync: false` variables in the Render dashboard:
   - `GROQ_API_KEY`
   - `DATABASE_URL`
   - `TAVILY_API_KEY`
   - `AVIATION_STACK_API_KEY`
   - `OPENWEATHER_API_KEY`
   - `LANGCHAIN_API_KEY` if tracing is enabled
5. Deploy and wait for the `/health` check to pass.

Render supplies `PORT`; the container command uses it automatically and binds to `0.0.0.0`.

## 6. MCP configuration

Tavily uses the remote MCP URL configured in `mcp_client.py` and needs `TAVILY_API_KEY`.

Weather runs the repository's `custom_weather_mcp_server.py` using the active Python interpreter and needs `OPENWEATHER_API_KEY`.

AviationStack runs `aviationstack-mcp` through `uvx` and needs `AVIATION_STACK_API_KEY`. The Docker image installs the `uv` package, which provides `uvx`. To use a custom executable, set `UVX_COMMAND`.

MCP calls have bounded retries and a short read cache. If a service is unavailable, the relevant agent produces a clearly labeled fallback instead of crashing the whole workflow.

## 7. Verify the deployment

```powershell
curl http://localhost:8000/health
curl http://localhost:8000/static/style.css
```

The health response should include `personalization_agent`, `safety_agent`, and `human_in_the_loop`.

Then open the root URL and verify:

1. A normal travel request creates a draft and pauses for approval.
2. A preference-rich request shows detected preferences.
3. A weather/outdoor request shows a safety assessment.
4. Rejecting a draft with weather feedback produces a revised final response.
5. The location control works only after browser permission and manual origin entry still works.

Check both operational endpoints:

```powershell
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

`/health` is a liveness check. `/ready` verifies that LLM and PostgreSQL
configuration is present. API routes accept `X-API-Key` or a bearer token when
`ROAMLY_API_KEY` is configured. The in-process rate limiter is suitable for a
single container; use a shared gateway or Redis-backed limiter when scaling to
multiple replicas.

## 8. Evaluation and evidence

The repository includes 60 labeled scenarios in `evaluation/dataset.json`,
covering normal planning, flights, hotels, weather, budgets, personalization,
safety, ambiguity, revisions, and unrelated requests. Run the live evaluator
only with real provider credentials:

```powershell
python evaluation/runner.py --mode live --output evaluation/results/live-run.json
```

The output contains one observed record per scenario and aggregate task,
structured-output, guardrail, latency, error, and MCP tool metrics. Cost per
request is reported as unknown unless token pricing is explicitly wired to the
provider. Hallucination rate is not claimed without semantic verification.
Never copy a metric into a resume until the corresponding JSON artifact exists.

Additional benchmarks:

```powershell
python evaluation/routing_runner.py --output evaluation/results/routing-run.json
python evaluation/runner.py --dataset evaluation/quality_dataset.json --output evaluation/results/quality-run.json
python evaluation/load_test.py --requests 10 --concurrency 5
python evaluation/mcp_runner.py --output evaluation/results/mcp-run.json
python evaluation/hitl_runner.py --output evaluation/results/hitl-run.json
python evaluation/security_runner.py --output evaluation/results/security-run.json
```

Routing uses exact labeled agent-set matching. Quality checks report heuristic
constraint/evidence coverage only; they do not prove factuality or hallucination
absence. The load probe measures the deployed HTTP boundary and should be run
against a controlled environment with a documented request count and concurrency.

Test levels are intentionally separate:

- Unit/regression tests use mocks for fast isolation and are not live-system evidence.
- Dataset validation checks labels and schemas without provider calls.
- Live evaluation uses the configured LLM, PostgreSQL checkpointing, and MCP services.
- Security evaluation records blocking, LLM-call count, tool-call count, and secret exposure checks.
- Load testing measures only the running HTTP environment named in the command.

The MCP evaluator distinguishes real tool success from real tool failure and
records safe fallback evidence separately. The in-process cache and rate limiter
are single-process controls; they are not distributed Redis-backed systems.

## 9. Common errors and fixes

### `DATABASE_URL is missing` or PostgreSQL connection errors

Set a reachable PostgreSQL URL. The database must allow external connections and the LangGraph database user must have permission to create checkpoint tables.

### Groq `model_not_found`

Set `GROQ_MODEL` to a model exposed by the configured Groq account. The default is `openai/gpt-oss-20b`, but model access is account-specific.

### `uvx was not found`

Install `uv`, ensure `uvx` is on `PATH`, or set `UVX_COMMAND` to its absolute path. Weather and hotel fallback behavior remains available if the flight MCP service is unavailable.

### Missing MCP API key

Set the relevant key in Render or `.env`. The affected agent reports unavailable live data and continues with labeled fallback guidance.

### Static files return 500

Use the repository's `app.py` asset route and current dependency versions. Do not replace it with an old Starlette `StaticFiles` setup in this Python/AnyIO environment.

### Requests hang or exceed provider limits

Check provider quotas and keep `MCP_RETRIES` bounded. The API limits concurrent graph runs to protect LLM, MCP, and PostgreSQL resources.
