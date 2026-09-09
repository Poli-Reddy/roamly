import os
import certifi
from dotenv import load_dotenv

load_dotenv()
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from typing import Any, TypedDict, Annotated
import operator
import uuid
import asyncio
import json
import psycopg
from psycopg.rows import dict_row
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command, interrupt
from langchain_core.messages import (
    AnyMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
)
from langchain_groq import ChatGroq


from mcp_client import (
    tavily_mcp_search,
    aviation_mcp_call,
    extract_destination,
    forecast_mcp_search,
    weather_mcp_search,
)


def get_database_url():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL is missing. "
            "Please add your Render PostgreSQL External Database URL to .env"
        )

    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"

    return database_url


GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Please add it to your .env file.")

# =========================
# LLM - original model kept
# =========================
llm = ChatGroq(
    model=GROQ_MODEL,
    api_key=GROQ_API_KEY,
)

# =========================
# State - original fields kept, new control fields added
# =========================
class TravelState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str
    origin_location: str

    # Supervisor + guardrail state
    guardrail_allowed: bool
    guardrail_reason: str
    selected_agents: list[str]
    trip_constraints: dict[str, Any]
    supervisor_reasoning: str

    # Original specialist results
    flight_results: str
    hotel_results: str
    weather_results: str
    itinerary: str

    # New budget + HITL state
    budget_results: str
    approval_request: str
    approved: bool
    human_feedback: str
    user_preferences: dict[str, Any]
    safety_analysis: str
    revision_in_progress: bool
    final_response: str

    llm_calls: int


# =========================
# Shared helpers
# =========================
KNOWN_AGENTS = {
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "personalization_agent",
    "itinerary_agent",
    "safety_agent",
}

AGENT_ORDER = [
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "personalization_agent",
    "itinerary_agent",
    "safety_agent",
]


def _llm_text(system_prompt: str, user_prompt: str) -> str:
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    return str(response.content)


def _json_from_llm(text: str) -> dict[str, Any]:
    """Extract the first complete JSON object returned by the model."""
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise ValueError("The model did not return a JSON object.")

    return json.loads(text[start : end + 1])


def _empty_constraints() -> dict[str, Any]:
    return {
        "destination": "",
        "origin": "",
        "duration": "",
        "budget": "",
        "travel_style": "",
        "special_preferences": [],
    }


def _fallback_selected_agents(query: str) -> list[str]:
    """Choose a conservative route when the supervisor response is unusable."""
    text = query.lower()
    selected = {"itinerary_agent"}
    keyword_groups = {
        "flight_agent": ("flight", "airport", "airline", "airfare"),
        "hotel_agent": ("hotel", "hostel", "accommodation", "stay"),
        "weather_agent": ("weather", "forecast", "climate", "packing"),
        "budget_agent": ("budget", "cost", "price", "afford", "under "),
        "personalization_agent": (
            "prefer", "vegetarian", "relaxed", "budget-friendly", "outdoor",
            "pace", "food", "activities",
        ),
        "safety_agent": ("safety", "risk", "rain", "unsafe", "weather", "outdoor"),
    }
    for agent, keywords in keyword_groups.items():
        if any(keyword in text for keyword in keywords):
            selected.add(agent)
    return [agent for agent in AGENT_ORDER if agent in selected]


# =========================
# Supervisor Agent + Input Guardrail
# =========================
def supervisor_agent(state: TravelState):
    query = state["user_query"]
    llm_calls = state.get("llm_calls", 0)

    guardrail_prompt = f"""
Determine whether the following request belongs to travel planning or travel
information. Valid requests can include destinations, flights, hotels, weather,
budgets, visas, transportation, sightseeing, food, packing, or itineraries.

Block clearly unrelated requests and requests asking for harmful or illegal
instructions. Do not block a valid travel request merely because some details
are missing.

Return strict JSON only:
{{
  "allowed": true,
  "reason": ""
}}

User request:
{query}
"""

    # Fail open on parser/model errors so a temporary JSON-format issue does not
    # break the original travel-planning behavior.
    try:
        guardrail_raw = _llm_text(
            "You are the input guardrail for a travel-planning application. "
            "Return strict JSON only.",
            guardrail_prompt,
        )
        guardrail_result = _json_from_llm(guardrail_raw)
        allowed = bool(guardrail_result.get("allowed", True))
        guardrail_reason = str(guardrail_result.get("reason", "")).strip()
        llm_calls += 1
    except Exception as exc:
        print(f"Guardrail fallback used: {exc}")
        allowed = True
        guardrail_reason = "Guardrail validation fallback allowed the request."

    if not allowed:
        reason = guardrail_reason or (
            "Roamly can only help with travel-planning requests. "
            "Please ask about a destination, flight, hotel, weather, budget, "
            "or itinerary."
        )
        return {
            "guardrail_allowed": False,
            "guardrail_reason": reason,
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": reason,
            "final_response": reason,
            "messages": [AIMessage(content=f"Guardrail blocked request: {reason}")],
            "llm_calls": llm_calls,
        }

    supervisor_prompt = f"""
You are the supervisor of a multi-agent travel-planning system.
Choose only the specialist agents needed for the request.

Available agents:
- flight_agent: flights, airports, airlines, routes, airfare, or booking advice
- hotel_agent: hotels, accommodation, neighborhoods, or places to stay
- weather_agent: weather, climate, season, forecast, or packing advice
- budget_agent: cost, affordability, price limits, or budget feasibility
- personalization_agent: personal preferences such as budget level, hotel category,
  transport, food, activities, pace, sightseeing style, or prior preferences
- itinerary_agent: creates the integrated travel plan and must always be included
- safety_agent: weather-related hazards, itinerary conflicts, unsafe timing,
  transport concerns, and destination-specific travel risks

Return strict JSON only using this schema:
{{
    "selected_agents": ["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "personalization_agent", "itinerary_agent", "safety_agent"],
  "trip_constraints": {{
    "destination": "",
    "origin": "",
    "duration": "",
    "budget": "",
    "travel_style": "",
    "special_preferences": []
  }},
  "reasoning": ""
}}

User request:
{query}
"""

    try:
        supervisor_raw = _llm_text(
            "You route work to travel specialist agents. Return strict JSON only.",
            supervisor_prompt,
        )
        parsed = _json_from_llm(supervisor_raw)
        requested_agents = parsed.get("selected_agents", [])
        if not isinstance(requested_agents, list):
            raise ValueError("selected_agents must be a list")
        requested_agents = {
            agent for agent in requested_agents if isinstance(agent, str)
        }
        requested_agents.add("itinerary_agent")
        selected_agents = [
            name for name in AGENT_ORDER
            if name in requested_agents and name in KNOWN_AGENTS
        ]

        constraints = _empty_constraints()
        parsed_constraints = parsed.get("trip_constraints", {})
        if not isinstance(parsed_constraints, dict):
            raise ValueError("trip_constraints must be an object")
        if isinstance(parsed_constraints, dict):
            for key in constraints:
                if key in parsed_constraints:
                    value = parsed_constraints[key]
                    constraints[key] = value if isinstance(value, (str, list)) else str(value)

        reasoning = str(parsed.get("reasoning", "")).strip()
        llm_calls += 1
    except Exception as exc:
        print(f"Supervisor fallback used: {exc}")
        # Original workflow behavior is preserved as the fallback.
        selected_agents = _fallback_selected_agents(query)
        constraints = _empty_constraints()
        reasoning = (
            "Supervisor parsing failed, so Roamly selected a conservative route "
            "based on the request and kept the itinerary agent enabled."
        )

    return {
        "guardrail_allowed": True,
        "guardrail_reason": guardrail_reason,
        "selected_agents": selected_agents,
        "trip_constraints": constraints,
        "supervisor_reasoning": reasoning,
        "messages": [AIMessage(content="Supervisor created the agent plan.")],
        "llm_calls": llm_calls,
    }


# =========================
# Guardrail blocked response
# =========================
def guardrail_blocked_agent(state: TravelState):
    reason = state.get("final_response") or state.get("guardrail_reason") or (
        "This request was blocked by the travel input guardrail."
    )
    return {
        "final_response": reason,
        "messages": [AIMessage(content=reason)],
    }


# =========================
# Flight Agent - original behavior kept
# =========================
FLIGHT_AGENT_PROMPT = """
You are a travel flight expert.

User Query:
{query}

Airport Information:
{airport_data}

Airline Information:
{airline_data}

Generate:
1. Likely departure airport
2. Likely arrival airport
3. Airlines serving this route
4. Typical flight duration
5. Estimated airfare range
6. Peak season pricing warning
7. Booking advice

Return concise travel guidance.
"""


def flight_agent(state: TravelState):
    print("\nINSIDE FLIGHT AGENT\n")
    query = state["user_query"]

    try:
        airports = asyncio.run(aviation_mcp_call("list_airports"))
        airlines = asyncio.run(aviation_mcp_call("list_airlines"))

        print("\nAIRPORTS:", airports)
        print("\nAIRLINES:", airlines)

        prompt = FLIGHT_AGENT_PROMPT.format(
            query=query,
            airport_data=str(airports)[:3000],
            airline_data=str(airlines)[:3000],
        )

        response = llm.invoke(
            [
                SystemMessage(content="You are an expert travel flight planner."),
                HumanMessage(content=prompt),
            ]
        )
        flight_data = response.content
    except Exception as exc:
        flight_data = f"Flight information unavailable: {exc}"

    return {
        "flight_results": flight_data,
        "messages": [AIMessage(content="Flight recommendations generated")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================
# Hotel Agent - original behavior kept
# =========================
def hotel_agent(state: TravelState):
    query = (
        f"Best hotels for "
        f"{state['user_query']}"
    )

    try:
        hotel_results = (
            "Source: live Tavily search results.\n"
            + str(asyncio.run(tavily_mcp_search(query)))
        )

    except Exception as exc:
        print(
            f"HOTEL AGENT MCP ERROR: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        hotel_results = (
            "Live hotel search is temporarily unavailable. "
            "Provide general accommodation and neighborhood "
            "guidance based on the destination and clearly "
            "label it as non-live advice."
        )

    return {
        "hotel_results": hotel_results,
        "messages": [
            AIMessage(
                content="Hotel information processed."
            )
        ],
        "llm_calls": (
            state.get("llm_calls", 0) + 1
        ),
    }


# =========================
# Weather Agent - original behavior kept
# =========================
def weather_agent(state: TravelState):
    city = extract_destination(
        state["user_query"]
    )

    try:
        weather_data = asyncio.run(
            weather_mcp_search(city)
        )

        forecast_data = asyncio.run(
            forecast_mcp_search(city)
        )

        weather_results = f"""
    Source: live OpenWeather MCP data.
Current Weather:
{weather_data}

Forecast:
{forecast_data}
"""

    except Exception as exc:
        print(
            f"WEATHER AGENT MCP ERROR: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        weather_results = (
            f"Live weather information for {city} "
            "is temporarily unavailable. Give general "
            "seasonal guidance and advise the traveler "
            "to verify the forecast before departure."
        )

    return {
        "weather_results": weather_results,
        "messages": [
            AIMessage(
                content="Weather information processed."
            )
        ],
    }


# =========================
# Budget Agent - new specialist
# =========================
def budget_agent(state: TravelState):
    prompt = f"""
Analyze whether this trip is realistic for the user's budget.

User Query:
{state['user_query']}

Previously Stored Preferences:
{state.get('user_preferences', {})}

Trip Constraints:
{state.get('trip_constraints', {})}

Flight Results:
{state.get('flight_results', '')}

Hotel Results:
{state.get('hotel_results', '')}

Weather Results:
{state.get('weather_results', '')}

Return:
1. Estimated cost categories
2. Budget risk areas
3. Money-saving suggestions
4. Overall feasibility

If exact live prices are unavailable, clearly label estimates as approximate.
"""

    response = llm.invoke(
        [
            SystemMessage(content="You are a practical travel budget analyst."),
            HumanMessage(content=prompt),
        ]
    )

    return {
        "budget_results": response.content,
        "messages": [AIMessage(content="Budget assessment generated.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================
# Personalization Agent
# =========================
def personalization_agent(state: TravelState):
    prompt = f"""
Extract the traveler's preferences from this request. Preserve explicit
preferences and use an empty string or empty list when a preference is absent.

User Query:
{state['user_query']}

Return strict JSON only with these keys:
{{
  "budget_level": "",
  "hotel_category": "",
  "transportation": "",
  "food_preferences": [],
  "activities": [],
  "travel_pace": "",
  "sightseeing_style": "",
  "previous_preferences": ""
}}
"""

    try:
        parsed = _json_from_llm(
            _llm_text(
                "You are a travel preference analyst. Return strict JSON only.",
                prompt,
            )
        )
        preferences = {
            "budget_level": str(parsed.get("budget_level", "")).strip(),
            "hotel_category": str(parsed.get("hotel_category", "")).strip(),
            "transportation": str(parsed.get("transportation", "")).strip(),
            "food_preferences": parsed.get("food_preferences", []),
            "activities": parsed.get("activities", []),
            "travel_pace": str(parsed.get("travel_pace", "")).strip(),
            "sightseeing_style": str(parsed.get("sightseeing_style", "")).strip(),
            "previous_preferences": str(
                parsed.get("previous_preferences", "")
            ).strip(),
        }
    except Exception as exc:
        print(f"Personalization fallback used: {exc}")
        preferences = {"extracted_from_request": state["user_query"]}

    return {
        "user_preferences": preferences,
        "messages": [AIMessage(content="Travel preferences extracted.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================
# Itinerary Agent - original behavior extended with selected results
# =========================
def itinerary_agent(state: TravelState):
    prompt = f"""
Create a complete travel itinerary.

User Query:
{state['user_query']}

Trip Constraints:
{state.get('trip_constraints', {})}

Flight Results:
{state.get('flight_results', '')}

Hotel Results:
{state.get('hotel_results', '')}

Weather Results:
{state.get('weather_results', '')}

Budget Results:
{state.get('budget_results', '')}

Traveler Preferences:
{state.get('user_preferences', {})}

Make the itinerary practical, budget-aware, personalized to the traveler's
preferences, and easy to follow. Do not invent preferences that are absent.
Create a clear draft that is ready for human review.
"""

    response = llm.invoke(
        [
            SystemMessage(content="You are an expert travel planner."),
            HumanMessage(content=prompt),
        ]
    )

    approval_request = (
        "Please review the generated draft itinerary. Approve it to create the "
        "final polished plan, or provide feedback for revision."
    )

    return {
        "itinerary": response.content,
        "approval_request": approval_request,
        "messages": [AIMessage(content="Draft itinerary created for human review.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================
# Safety and Risk Analysis Agent
# =========================
def safety_agent(state: TravelState):
    prompt = f"""
Assess travel safety risks using only the information below. Do not invent
live alerts or claim certainty when data is unavailable.

User Request:
{state['user_query']}

Weather Information:
{state.get('weather_results', '')}

Draft Itinerary:
{state.get('itinerary', '')}

Return a concise assessment with exactly these headings:
Overall Risk: LOW, MEDIUM, or HIGH
Weather:
Itinerary Risk:
Transportation:
Recommendation:
"""

    try:
        response = llm.invoke(
            [
                SystemMessage(content="You are a careful travel safety analyst."),
                HumanMessage(content=prompt),
            ]
        )
        analysis = str(response.content)
    except Exception as exc:
        print(f"Safety analysis fallback used: {exc}")
        analysis = (
            "Overall Risk: UNKNOWN\n"
            "Weather: Live risk data is unavailable.\n"
            "Itinerary Risk: Verify activities against the latest forecast.\n"
            "Transportation: Check local advisories before departure.\n"
            "Recommendation: Reconfirm weather and transport conditions before travel."
        )

    return {
        "safety_analysis": analysis,
        "messages": [AIMessage(content="Travel safety analysis generated.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================
# Human-in-the-Loop approval
# =========================
def human_approval_agent(state: TravelState):
    # Do not wrap interrupt() in try/except. LangGraph uses it to pause execution.
    review = interrupt(
        {
            "question": "Do you approve this itinerary?",
            "draft_itinerary": state.get("itinerary", ""),
            "approval_request": state.get("approval_request", ""),
            "selected_agents": state.get("selected_agents", []),
            "supervisor_reasoning": state.get("supervisor_reasoning", ""),
            "expected_response": {
                "approved": True,
                "feedback": "Optional revision feedback",
            },
        }
    )

    approved = bool(review.get("approved", False))
    human_feedback = str(review.get("feedback", "")).strip()
    feedback_lower = human_feedback.lower()
    preference_revision = any(
        term in feedback_lower
        for term in (
            "preference",
            "vegetarian",
            "hotel",
            "budget",
            "pace",
            "activity",
        )
    )
    safety_revision = any(
        term in feedback_lower
        for term in (
            "weather",
            "rain",
            "unsafe",
            "safety",
            "risk",
            "outdoor",
            "transport",
        )
    )

    return {
        "approved": approved,
        "human_feedback": human_feedback,
        "revision_in_progress": not approved,
        "selected_agents": (
            [
                agent
                for agent in AGENT_ORDER
                if agent == "itinerary_agent"
                or (agent == "personalization_agent" and preference_revision)
                or (agent == "safety_agent" and safety_revision)
            ]
            if not approved
            else state.get("selected_agents", [])
        ),
        "messages": [AIMessage(content="Human approval step completed.")],
    }


# =========================
# Final Response Agent - original format kept, HITL feedback added
# =========================
def final_agent(state: TravelState):
    if state.get("approved", False):
        review_instruction = (
            "The user approved the draft. Preserve its decisions while polishing it."
        )
    else:
        review_instruction = f"""
The user requested a revision. Apply this feedback carefully:
{state.get('human_feedback', '') or 'Improve the draft before finalizing it.'}
"""

    final_prompt = f"""
Generate the final travel response for the user.

Human Review:
{review_instruction}

User Request:
{state['user_query']}

Supervisor Constraints:
{state.get('trip_constraints', {})}

Flights:
{state.get('flight_results', '')}

Hotels:
{state.get('hotel_results', '')}

Weather:
{state.get('weather_results', '')}

Budget Analysis:
{state.get('budget_results', '')}

Traveler Preferences:
{state.get('user_preferences', {})}

Safety and Risk Analysis:
{state.get('safety_analysis', '')}

Draft Itinerary:
{state.get('itinerary', '')}

Format the final answer beautifully using these sections:
1. Trip Summary
2. Flight Information
3. Hotel Suggestions
4. Weather Information
5. Day-by-Day Itinerary
6. Estimated Budget
7. Personalization Notes
8. Safety and Risk Assessment
9. Final Recommendations

Important:
- Be clear and practical.
- Mention that live flight APIs may not provide ticket prices when pricing is unavailable.
- Include weather-based travel advice.
- Clearly reflect relevant traveler preferences.
- Include the safety risk level, reasons, and recommendations.
- Keep the response useful for real travel planning.
- Incorporate the human feedback when revision was requested.
"""

    response = llm.invoke(
        [
            SystemMessage(
                content="You are a professional AI travel booking assistant."
            ),
            HumanMessage(content=final_prompt),
        ]
    )

    return {
        "final_response": response.content,
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# =========================
# Dynamic Supervisor Routing
# =========================
ROUTE_MAP = {
    "guardrail_blocked": "guardrail_blocked",
    "flight_agent": "flight_agent",
    "hotel_agent": "hotel_agent",
    "weather_agent": "weather_agent",
    "budget_agent": "budget_agent",
    "personalization_agent": "personalization_agent",
    "itinerary_agent": "itinerary_agent",
    "safety_agent": "safety_agent",
}


def _selected_agents(state: TravelState) -> list[str]:
    selected = state.get("selected_agents", [])
    return [agent for agent in AGENT_ORDER if agent in selected]


def route_from_supervisor(state: TravelState) -> str:
    if not state.get("guardrail_allowed", True):
        return "guardrail_blocked"

    selected = _selected_agents(state)
    return selected[0] if selected else "itinerary_agent"


def route_after_agent(current_agent: str):
    def route(state: TravelState) -> str:
        selected = _selected_agents(state)
        current_index = AGENT_ORDER.index(current_agent)

        for next_agent in AGENT_ORDER[current_index + 1 :]:
            if next_agent in selected:
                return next_agent

        return "itinerary_agent"

    return route


def route_after_itinerary(state: TravelState) -> str:
    if "safety_agent" in _selected_agents(state):
        return "safety_agent"
    if state.get("revision_in_progress"):
        return "final_agent"
    return "human_approval"


def route_after_safety(state: TravelState) -> str:
    return "final_agent" if state.get("revision_in_progress") else "human_approval"


def route_after_review(state: TravelState) -> str:
    if state.get("approved", False):
        return "final_agent"

    selected = _selected_agents(state)
    return "personalization_agent" if "personalization_agent" in selected else "itinerary_agent"


# =========================
# Build Graph
# =========================
graph = StateGraph(TravelState)

graph.add_node("supervisor", supervisor_agent)
graph.add_node("guardrail_blocked", guardrail_blocked_agent)
graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("weather_agent", weather_agent)
graph.add_node("budget_agent", budget_agent)
graph.add_node("personalization_agent", personalization_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("safety_agent", safety_agent)
graph.add_node("human_approval", human_approval_agent)
graph.add_node("final_agent", final_agent)

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", route_from_supervisor, ROUTE_MAP)

graph.add_conditional_edges(
    "flight_agent", route_after_agent("flight_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "hotel_agent", route_after_agent("hotel_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "weather_agent", route_after_agent("weather_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "budget_agent", route_after_agent("budget_agent"), ROUTE_MAP
)
graph.add_conditional_edges(
    "personalization_agent", route_after_agent("personalization_agent"), ROUTE_MAP
)

graph.add_conditional_edges(
    "itinerary_agent",
    route_after_itinerary,
    {"safety_agent": "safety_agent", "human_approval": "human_approval"},
)
graph.add_conditional_edges(
    "safety_agent",
    route_after_safety,
    {"human_approval": "human_approval", "final_agent": "final_agent"},
)
graph.add_conditional_edges(
    "human_approval",
    route_after_review,
    {
        "personalization_agent": "personalization_agent",
        "itinerary_agent": "itinerary_agent",
        "final_agent": "final_agent",
    },
)
graph.add_edge("final_agent", END)
graph.add_edge("guardrail_blocked", END)

# =========================
# PostgreSQL Checkpointer - original persistence kept
# =========================
DATABASE_URL = get_database_url()
_conn = psycopg.connect(
    DATABASE_URL,
    autocommit=True,
    row_factory=dict_row,
)
checkpointer = PostgresSaver(_conn)
checkpointer.setup()

travel_graph = graph.compile(checkpointer=checkpointer)


# =========================
# FastAPI-facing helpers
# =========================
def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__", [])
    if not interrupts:
        return None

    first_interrupt = interrupts[0]
    payload = getattr(first_interrupt, "value", first_interrupt)
    return payload if isinstance(payload, dict) else {"value": payload}


def _serialize_result(
    result: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    messages = result.get("messages", [])
    last_message = messages[-1].content if messages else ""
    answer = result.get("final_response") or last_message
    interrupt_payload = _interrupt_payload(result)

    if interrupt_payload:
        answer = interrupt_payload.get("draft_itinerary") or result.get(
            "itinerary", ""
        )

    return {
        "thread_id": thread_id,
        "answer": answer,
        "requires_approval": interrupt_payload is not None,
        "approval_request": (
            interrupt_payload.get("approval_request", "")
            if interrupt_payload
            else result.get("approval_request", "")
        ),
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "weather_results": result.get("weather_results", ""),
        "budget_results": result.get("budget_results", ""),
        "user_preferences": result.get("user_preferences", {}),
        "safety_analysis": result.get("safety_analysis", ""),
        "itinerary": (
            interrupt_payload.get("draft_itinerary", "")
            if interrupt_payload
            else result.get("itinerary", "")
        ),
        "selected_agents": result.get("selected_agents", []),
        "trip_constraints": result.get("trip_constraints", {}),
        "origin_location": result.get("origin_location", ""),
        "supervisor_reasoning": result.get("supervisor_reasoning", ""),
        "guardrail_allowed": result.get("guardrail_allowed", True),
        "guardrail_reason": result.get("guardrail_reason", ""),
        "approved": result.get("approved"),
        "human_feedback": result.get("human_feedback", ""),
        "revision_in_progress": result.get("revision_in_progress", False),
        "llm_calls": result.get("llm_calls", 0),
    }


def run_travel_agent(
    user_input: str,
    thread_id: str | None = None,
    origin_location: str | None = None,
):
    """Start a new travel-planning run and pause at human approval."""
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config = {"configurable": {"thread_id": thread_id}}
    origin_context = (
        f"\n\nDetected starting point: {origin_location}. "
        "Plan the journey from this location to the requested destination "
        "unless the user explicitly provides a different origin."
        if origin_location
        else ""
    )

    stored_preferences: dict[str, Any] = {}
    if thread_id:
        try:
            snapshot = travel_graph.get_state(config)
            snapshot_values = getattr(snapshot, "values", {}) or {}
            previous = snapshot_values.get("user_preferences", {})
            if isinstance(previous, dict):
                stored_preferences = previous
        except Exception as exc:
            print(f"Preference memory lookup skipped: {exc}")

    result = travel_graph.invoke(
        {
            "messages": [HumanMessage(content=user_input)],
            "user_query": user_input + origin_context,
            "origin_location": origin_location or "",
            "guardrail_allowed": True,
            "guardrail_reason": "",
            "selected_agents": [],
            "trip_constraints": _empty_constraints(),
            "supervisor_reasoning": "",
            "flight_results": "",
            "hotel_results": "",
            "weather_results": "",
            "budget_results": "",
            "user_preferences": stored_preferences,
            "safety_analysis": "",
            "itinerary": "",
            "approval_request": "",
            "approved": False,
            "human_feedback": "",
            "revision_in_progress": False,
            "final_response": "",
            "llm_calls": 0,
        },
        config=config,
    )

    return _serialize_result(result, thread_id)


def resume_travel_agent(
    thread_id: str,
    approved: bool,
    feedback: str = "",
):
    """Resume the paused LangGraph thread after human review."""
    if not thread_id:
        raise ValueError("thread_id is required to resume a travel plan.")

    config = {"configurable": {"thread_id": thread_id}}
    result = travel_graph.invoke(
        Command(
            resume={
                "approved": approved,
                "feedback": feedback.strip(),
            }
        ),
        config=config,
    )

    return _serialize_result(result, thread_id)
