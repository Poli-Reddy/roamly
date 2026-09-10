# Roamly Test Results

## Report Metadata

- Report date: 2026-09-10
- Current repository commit: `7ab79b6` (`production ready`)
- Current branch: `main`
- Repository: `Poli-Reddy/roamly`
- Python runtime used locally: Python 3.14.3
- Evaluation evidence policy: no fabricated results; unavailable evaluations are marked `NOT EXECUTED`.
- Important reproducibility note: the preserved live JSON artifacts were generated before the final GitHub rebase and contain Git SHA `d1f2e54657dd31745cdcaa5353ea5e10da078e15`. Regenerate live artifacts after code changes when exact current-commit provenance is required.

## Executive Summary

| Area | Result | Classification |
|---|---:|---|
| Python compilation | PASS | Deterministic static check |
| Dataset schema validation | PASS | Deterministic validation, no providers |
| Unit/regression tests | 26/26 passed | Isolated tests; mocks are intentional |
| Frontend homepage smoke test | HTTP 200 | Local running application |
| Backend health smoke test | HTTP 200 | Local running application |
| MCP live scenario correctness | 80% | Real MCP services, 5 scenarios |
| MCP expected-success pass rate | 75% | Real MCP services, 4 expected-success scenarios |
| MCP expected-failure handling | 100% | Real invalid-input scenario |
| Security decision accuracy | 100% | Real configured supervisor, 15 scenarios |
| Full 60-case end-to-end evaluation | NOT EXECUTED | Interrupted by a real Groq request timeout |
| Full 50-case routing evaluation | NOT EXECUTED | PostgreSQL connection dropped, retry was interrupted |
| Live HITL evaluation | NOT EXECUTED | Requires a complete live run |
| HTTP load evaluation | NOT EXECUTED | Requires a controlled load-test run |
| Semantic groundedness/hallucination rate | NOT CLAIMED | No semantic verifier is implemented |
| Cost per request | NOT CLAIMED | Provider pricing/token accounting is not wired in |

## Commands Executed

```powershell
$files = @('app.py','backend.py','mcp_client.py') + (Get-ChildItem evaluation -Filter *.py | ForEach-Object { $_.FullName })
python -m py_compile $files
python evaluation/validate_datasets.py
python -m unittest discover -s tests -v
```

Most recent output:

```text
dataset_validation=ok
main=60 routing=50 quality=10 security=15
Ran 26 tests in 0.695s
OK
```

Frontend/backend smoke check:

```text
GET http://127.0.0.1:8000/health -> 200
GET http://127.0.0.1:8000/ -> 200
homepage contains Roamly -> True
```

## Unit and Regression Tests

Command:

```powershell
python -m unittest discover -s tests -v
```

Result: **26/26 passed**.

These are Level 1 unit/regression tests. They do not prove that the real LLM, real MCP services, or real PostgreSQL behave correctly end to end. Mocks and patches are appropriate here because these tests isolate application logic.

### Historical deterministic test runs

These earlier counts are included for traceability, but the current authoritative result is 26/26:

| Stage | Result | Context |
|---|---:|---|
| Baseline before new security tests | 20/20 passed | Before deterministic injection regression coverage |
| After injection test | 21/21 passed | Added prompt-injection blocking regression |
| After fail-closed guardrail test | 22/22 passed | Added malformed guardrail-output coverage |
| After API/MCP boundary tests | 24/24 passed | Added API auth/rate-limit and MCP error-payload tests |
| Current run | 26/26 passed | Added invalid-input retry and API exception-redaction coverage |

### EnvironmentTests: 3 passed

| Test | What it verifies | External services? |
|---|---|---|
| `test_env_contains_required_runtime_settings_without_placeholders` | Required `.env` keys exist and are not placeholder values | No live call |
| `test_git_ignores_env_file` | `.env` is ignored by Git | No |
| `test_runtime_controls_are_configured` | MCP retry and cache settings are configured | No |

### AgentTests: 11 passed

| Test | What it verifies | Test type |
|---|---|---|
| `test_personalization_extracts_structured_preferences` | Preference JSON is parsed into state | Unit with fake LLM |
| `test_personalization_degrades_on_invalid_llm_json` | Invalid preference JSON uses fallback state | Unit with patched LLM |
| `test_flight_agent_degrades_when_mcp_fails` | Flight agent returns labeled fallback when MCP fails | Unit with mocked MCP/LLM |
| `test_hotel_agent_degrades_when_search_fails` | Hotel agent returns unavailable-data fallback | Unit with mocked MCP |
| `test_weather_agent_degrades_when_weather_fails` | Weather agent returns labeled unavailable-data fallback | Unit with mocked MCP |
| `test_budget_itinerary_and_final_agents_consume_llm` | Budget, itinerary, and final agents consume prior state | Unit with fake LLM |
| `test_safety_agent_uses_existing_weather_and_itinerary_and_has_fallback` | Safety agent uses existing evidence and has failure fallback | Unit with fake/failing LLM |
| `test_mcp_invocation_retries_then_caches_success` | Transient MCP failure retries, then successful result is cached | Unit with mocked tool |
| `test_mcp_tool_error_payload_counts_as_failure` | MCP error payload is not counted as successful execution | Unit with mocked tool |
| `test_invalid_mcp_input_does_not_retry` | Invalid arguments fail before a tool call and do not retry | Unit with mocked tool |
| `test_thread_reuses_stored_preferences` | Existing thread preferences are passed into a new graph run | Unit with patched graph |

### RoutingTests: 5 passed

| Test | What it verifies | Test type |
|---|---|---|
| `test_supervisor_selects_new_agents_from_model_output` | Valid supervisor output is filtered and ordered | Unit with patched LLM |
| `test_guardrail_blocks_unrelated_requests` | Unrelated request is blocked | Unit with patched LLM |
| `test_guardrail_blocks_prompt_injection_before_llm` | Known injection pattern is blocked before an LLM call | Unit; LLM call asserted absent |
| `test_guardrail_fails_closed_when_model_output_is_invalid` | Malformed guardrail output blocks instead of failing open | Unit with invalid model output |
| `test_routing_order_and_hitl_revision_scope` | Agent ordering and HITL revision routing are correct | Deterministic routing unit test |

### ApiTests: 7 passed

| Test | What it verifies | Test type |
|---|---|---|
| `test_api_key_authentication_and_rate_limit_boundary` | Bearer auth, invalid auth, 429 response, and `Retry-After` | ASGI boundary test with patched config |
| `test_api_validation_and_health` | Health response and empty-message validation | Direct API test |
| `test_api_delegates_draft_and_approval` | Draft and approval endpoints delegate correctly | Direct API test with patched services |
| `test_api_hides_internal_exception_details` | Client receives generic 500 and no secret-bearing exception | Direct API test with patched failure |
| `test_api_rejects_oversized_inputs` | Message and feedback length limits | Pydantic validation test |
| `test_static_assets_and_path_traversal_protection` | Static asset serving and `../.env` protection | Direct route test |
| `test_reverse_location_validates_coordinates` | Invalid coordinates are rejected and valid lookup is handled | Direct route test with mocked HTTP only |

## Dataset Validation

Command:

```powershell
python evaluation/validate_datasets.py
```

Result: **PASS**.

```text
main=60
routing=50
quality=10
security=15
MCP and HITL structured datasets also validated
```

The validator checks non-empty and unique IDs, prompt presence, routing agent allowlists, required `itinerary_agent`, structured quality constraints, security labels, MCP server/tool/argument fields, and HITL action/feedback/revision labels. It does not call an LLM, MCP service, or database.

## Live MCP Evaluation

Artifact:

- `evaluation/results/mcp-audit-20260910-final.json`

Method: real configured MCP services; no mocks.

Services/tools evaluated:

| Scenario | Server/tool | Expected | Observed | Correct? |
|---|---|---|---|---|
| `mcp-weather-valid` | Weather / `get_current_weather` | Success | Success | Yes |
| `mcp-weather-forecast` | Weather / `get_forecast` | Success | Success | Yes |
| `mcp-weather-invalid` | Weather / `get_current_weather` with empty city | Failure | Validation failure before tool call | Yes |
| `mcp-tavily-valid` | Tavily / `tavily_search` | Success | Success | Yes |
| `mcp-aviation-airports` | AviationStack / `list_airports` | Success | Failure | No |

Observed metrics:

| Metric | Value |
|---|---:|
| Total scenarios | 5 |
| Expected-success scenarios | 4 |
| Expected-success pass rate | 75% |
| Expected-failure scenarios | 1 |
| Expected-failure handling rate | 100% |
| Scenario correctness rate | 80% |
| Real MCP attempts | 4 |
| Real MCP successes | 3 |
| Attempt-level tool success rate | 75% |
| Safe expected failures | 1 |
| Unexpected success-case failures | 1 |
| Failure category | `DEPENDENCY_UNAVAILABLE: 1` |

The AviationStack failure was caused by missing local `uvx`, not by a fabricated result or a counted fallback success. The Dockerfile now runs `uvx --version` during image build so this dependency fails earlier in deployment.

The invalid weather scenario is correctly excluded from expected-success performance. Its validation error caused zero external tool attempts and no retries.

## Live Security Evaluation

Artifact:

- `evaluation/results/security-audit-20260910-expanded.json`

Method: real configured supervisor boundary with real LLM access for cases that reach the model. It does not execute the complete graph for every case.

Observed metrics:

| Metric | Value |
|---|---:|
| Scenarios | 15 |
| Decision accuracy | 100% |
| Secret exposure count | 0 |
| Full-application unexpected tool calls | Not claimed |

Scenario coverage:

- Prompt instruction override
- Guardrail bypass
- Tool invocation forcing
- Environment/API-key exposure
- Supervisor restriction bypass
- System/developer impersonation
- Indirect injection wording
- Tool-argument manipulation
- Valid travel requests that must remain allowed

Important scope detail:

- The deterministic attack cases that match the pre-LLM patterns recorded `llm_calls=0` and no selected agents.
- Some attack cases were blocked by the LLM guardrail after one LLM call. They were still correctly blocked, but should not be described as all attacks being blocked before LLM processing.
- `unexpected_tool_calls` is intentionally recorded as not claimed because this runner evaluates the supervisor boundary, not the complete graph/MCP execution path.

## Frontend and Backend Smoke Test

Last verified against the running local server:

```text
GET /health -> HTTP 200
GET / -> HTTP 200
Homepage contains Roamly -> True
```

The frontend is served by FastAPI; there is no separate frontend build process. The browser UI is delivered from `templates/index.html`, `static/script.js`, and `static/style.css`.

## API Security and Validation Coverage

Verified by unit/ASGI tests:

- Optional API-key authentication using `X-API-Key` or Bearer auth
- Timing-safe API-key comparison
- Invalid key returns 401
- Valid key reaches route validation
- Rate limit returns 429
- `Retry-After` header is present
- Request IDs are attached to responses/logs
- Empty message rejected
- Oversized message rejected
- Oversized approval feedback rejected
- Invalid coordinates rejected
- Static path traversal rejected
- Raw backend exceptions hidden from clients
- Credential-shaped log values redacted
- Production mode requires `ROAMLY_API_KEY`

The rate limiter is in-process and suitable only for a single process/container. It is not a distributed limiter.

## MCP Reliability and Cache Coverage

Verified in isolated tests:

- Retry after a transient failure
- No retry for invalid input
- MCP error payload is classified as failure
- Successful result cache hit
- Cache metrics and per-attempt event telemetry
- Tool/server allowlist enforcement
- Basic MCP argument schema validation

The live MCP artifact records per-call server, tool, attempt, latency, cache status, error category, and bounded evidence. The cache remains process-local; no Redis/distributed cache is implemented.

## HITL Evaluation

Implemented but not executed live:

- Dataset: 12 scenarios in `evaluation/hitl_dataset.json`
- Runner: `evaluation/hitl_runner.py`
- Covers approval, preference revision, safety revision, budget revision, hotel revision, pace, activity safety, food, transport, multiple changes, repeated revision, and final approval.
- Structured `required_changes` labels are present.
- Live execution requires real Groq, PostgreSQL checkpointing, MCP services where routed, and a complete interrupt/resume workflow.

Status: **NOT EXECUTED - no valid live HITL result artifact is present.**

## Routing Evaluation

Implemented but not completed across the full dataset:

- Dataset: 50 labeled scenarios in `evaluation/routing_dataset.json`
- Runner: `evaluation/routing_runner.py`
- Method: exact ordered selected-agent-set match
- `itinerary_agent` is required by the dataset policy
- Partial-set F1 is not currently reported

A one-case routing smoke artifact existed previously but was removed during cleanup because it was not a full benchmark result. The full routing run later encountered a PostgreSQL checkpoint connection drop and was not completed.

Status: **NOT EXECUTED - do not claim routing accuracy.**

## Main End-to-End Evaluation

Implemented but not completed:

- Dataset: 60 scenarios across normal, flights, hotels, weather, budget, personalization, safety, ambiguity, revision, and guardrail categories.
- Runner: `evaluation/runner.py`
- Real LLM, PostgreSQL, and MCP path required.
- Quality scoring is explicitly heuristic term/evidence coverage, not semantic correctness.

A full run began and produced several successful real records, but it was interrupted during a real Groq request timeout. No complete result artifact was retained.

Status: **NOT EXECUTED - no complete 60-case result should be reported.**

The following discarded attempts are not metrics: an earlier quality run failed
with a dataset-label `KeyError`, an earlier security artifact temporarily showed
0% only because the evaluator attempted to JSON-serialize `AIMessage` objects,
and an earlier full run was interrupted during a real provider timeout. Those
evaluator defects were fixed and the incomplete/invalid artifacts were removed.

## Quality Evaluation

Implemented but not completed across all 10 scenarios:

- Dataset: `evaluation/quality_dataset.json`
- Runner: `evaluation/runner.py --dataset evaluation/quality_dataset.json`
- Measures constraint term coverage and tool-evidence term coverage.
- These are heuristic evidence metrics only.
- They are not hallucination detection, factuality verification, semantic correctness, or groundedness proof.

The earlier one-case quality smoke result was removed during cleanup because it was not a complete benchmark.

Status: **NOT EXECUTED - no full quality metric should be claimed.**

## Load Evaluation

Implemented but not executed:

- Runner: `evaluation/load_test.py`
- Measures HTTP status, success rate, p50 latency, p95 latency, concurrency, and status counts.
- It does not measure AI quality or MCP quality.
- It must be run against a controlled deployment/environment.

Recommended commands:

```powershell
python evaluation/load_test.py --url http://127.0.0.1:8000/api/travel --requests 10 --concurrency 5
python evaluation/load_test.py --url http://127.0.0.1:8000/api/travel --requests 25 --concurrency 5
python evaluation/load_test.py --url http://127.0.0.1:8000/api/travel --requests 50 --concurrency 10
```

Status: **NOT EXECUTED - no load numbers should be claimed.**

## CI/CD

Configured in `.github/workflows/ci.yml`:

1. Checkout
2. Python 3.11 setup
3. Dependency installation
4. Dummy CI environment configuration
5. Python compilation
6. Dataset validation
7. Unit/regression tests

The CI dummy credentials are only for isolated tests. They are not evidence of real LLM, MCP, or PostgreSQL integration.

GitHub Actions execution was not run from this local environment, so CI status is **NOT LOCALLY VERIFIED**.

## Static and Dependency Security Scans

Not currently executed:

- Ruff linting
- Bandit security scan
- pip-audit dependency scan
- Coverage report

These should be added/executed separately before making static-analysis or coverage claims.

## Repository Hygiene

Verified:

- `.env` is ignored and was not committed.
- Generated `__pycache__` folders were removed.
- Superseded evaluation artifacts were removed.
- Only the latest MCP and expanded security artifacts were preserved.
- No result JSON files are tracked by Git because `evaluation/results/` is ignored.
- `demo.excalidraw` was retained as project design documentation.

## Resume-Safe Claims

Currently defensible claims from completed evidence:

- Implemented and regression-tested a multi-agent travel system with **26 passing isolated tests** covering routing, guardrails, prompt injection, API validation, authentication, rate limiting, HITL routing, MCP retry/cache behavior, failure handling, and path traversal.
- Executed a real MCP evaluation across **5 scenarios**, with **75% expected-success pass rate**, **100% expected-failure handling**, and one dependency-unavailable AviationStack failure caused by missing `uvx`.
- Evaluated **15 security scenarios** with **100% labeled allow/block decision accuracy** and **0 detected secret exposures** at the supervisor boundary.
- Validated evaluation datasets containing **60 main**, **50 routing**, **10 quality**, and **15 security** scenarios, plus structured MCP/HITL datasets.

## Claims That Must Not Be Made Yet

Do not claim any of the following until the corresponding full live artifacts exist:

- Overall end-to-end task-completion percentage
- Full 50-case routing accuracy
- Full 10-case quality/constraint score
- HITL workflow completion or revision-adherence percentage
- HTTP p50/p95 load numbers
- Hallucination rate or groundedness percentage
- Cost per request
- 100% application-wide unexpected-tool-call prevention
- Distributed rate limiting
- Distributed caching
- 100% MCP reliability
- 100% fallback correctness
