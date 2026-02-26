"""Tool schemas and dispatch layer for TravelMate.

Each tool:
  - Has an OpenAI-compatible JSON schema (works via LiteLLM for Claude + OpenAI)
  - Has an async implementation function
  - Runs inside a Chainlit Step for transparent step visibility
"""

from __future__ import annotations

import json
import asyncio
import logging
from typing import Any

import chainlit as cl
import litellm

logger = logging.getLogger(__name__)

from config.settings import get_settings
from models.travel_models import TravelRequest


def _get_active_model() -> str:
    """Return the model selected for this session, falling back to the default from settings."""
    try:
        model = cl.user_session.get("active_model")
        if model:
            return model
    except Exception:
        pass
    return get_settings().llm_model


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "parse_travel_request",
            "description": "Extract structured travel params from natural language. Call FIRST.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_message": {"type": "string"},
                },
                "required": ["user_message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_flights",
            "description": "Search available flights. Run in parallel with get_weather.",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin":       {"type": "string", "description": "IATA code"},
                    "destination":  {"type": "string", "description": "IATA code"},
                    "depart_date":  {"type": "string", "description": "YYYY-MM-DD"},
                    "return_date":  {"type": "string", "description": "YYYY-MM-DD or null"},
                    "num_adults":   {"type": "integer"},
                    "max_results":  {"type": "integer", "default": 5},
                },
                "required": ["origin", "destination", "depart_date", "num_adults"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotels",
            "description": "Search hotels. Use check_in=depart_date, check_out=return_date exactly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city_code":   {"type": "string", "description": "IATA city code"},
                    "check_in":    {"type": "string", "description": "YYYY-MM-DD"},
                    "check_out":   {"type": "string", "description": "YYYY-MM-DD"},
                    "num_adults":  {"type": "integer"},
                    "max_results": {"type": "integer", "default": 3},
                },
                "required": ["city_code", "check_in", "check_out", "num_adults"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get destination weather. Run in parallel with search_flights.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["city", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_budget",
            "description": "Calculate total trip cost and validate against user budget.",
            "parameters": {
                "type": "object",
                "properties": {
                    "flight_price_usd":         {"type": "number"},
                    "hotel_price_per_night_usd": {"type": "number"},
                    "num_nights":               {"type": "integer"},
                    "num_people":               {"type": "integer"},
                    "daily_expenses_usd":        {"type": "number", "description": "Per person/day"},
                    "budget_usd":               {"type": "number"},
                },
                "required": [
                    "flight_price_usd", "hotel_price_per_night_usd",
                    "num_nights", "num_people", "daily_expenses_usd",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotel_details",
            "description": "Web-search all hotels at once for website URL and description.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hotel_names": {"type": "array", "items": {"type": "string"}},
                    "city":        {"type": "string"},
                },
                "required": ["hotel_names", "city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "format_itinerary",
            "description": "Produce the final polished markdown itinerary with images and links.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination":    {"type": "string"},
                    "dates":          {"type": "object"},
                    "flights":        {"type": "array", "items": {"type": "object"}, "description": "All available flights from search_flights result"},
                    "recommended_flight_index": {"type": "integer", "description": "Index of the best-value flight in the flights array"},
                    "hotels":         {"type": "array", "items": {"type": "object"}},
                    "weather":        {"type": "object"},
                    "budget_summary": {"type": "object"},
                    "tips":           {"type": "array", "items": {"type": "string"}},
                },
                "required": ["destination", "dates"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _extract_json_object(text: str) -> dict:
    """Robustly extract a JSON object from LLM output.

    Handles three common cases:
      1. Pure JSON                   → {"key": "value"}
      2. Markdown code fence         → ```json\\n{...}\\n```
      3. JSON embedded in prose      → "Here you go: {...} enjoy!"
    """
    import re
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find the first {...} block in the text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"No valid JSON object found in LLM response: {text[:200]}")


async def _tool_parse_travel_request(args: dict) -> dict:
    """Use LLM with JSON mode to extract structured params from free text."""
    import datetime
    today = datetime.date.today().isoformat()

    # Inject the user's saved profile as additional context
    profile = ""
    try:
        profile = cl.user_session.get("profile") or ""
    except Exception:
        pass
    profile_section = (
        f'\n\nUser profile (use as defaults when not specified by the user):\n"{profile}"'
        if profile
        else ""
    )

    response = await litellm.acompletion(
        model=_get_active_model(),
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract travel parameters from the user's request and return ONLY valid JSON "
                    "with these fields:\n"
                    "- origin (IATA airport code of the PRIMARY departure airport, e.g. SFO, JFK — if multiple airports mentioned pick the major one)\n"
                    "- destination (IATA airport code for the PRIMARY/FIRST destination)\n"
                    "- city_code (IATA city code for the primary destination, usually same as destination airport)\n"
                    "- depart_date (YYYY-MM-DD) — the day the traveler flies out\n"
                    "- return_date (YYYY-MM-DD or null) — the day the traveler flies back\n"
                    "- check_in (YYYY-MM-DD) — MUST equal depart_date exactly\n"
                    "- check_out (YYYY-MM-DD or null) — MUST equal return_date exactly\n"
                    "- num_adults (integer, default 2)\n"
                    "- budget_usd (number or null)\n"
                    "- preferences (list of strings — include travel style, interests, AND any additional cities/stops)\n\n"
                    "CRITICAL: check_in must be identical to depart_date and check_out must be identical to "
                    "return_date. Never compute them separately.\n\n"
                    "For multi-city trips (e.g. Tokyo + Kyoto), set destination to the first city and "
                    "list subsequent cities in preferences (e.g. 'also visiting Kyoto for 4 nights').\n\n"
                    f"Today's date is {today}. Infer reasonable dates if only a month is mentioned — "
                    "use the next upcoming occurrence of that month.\n\n"
                    "Return ONLY the JSON object, no explanation."
                    + profile_section
                ),
            },
            {"role": "user", "content": args["user_message"]},
        ],
        response_format={"type": "json_object"},
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise ValueError(
            "LLM returned empty response for travel request parsing. "
            "Try rephrasing your request with a clear origin, destination, and dates."
        )
    return _extract_json_object(content)


async def _tool_search_flights(args: dict, cl_msg) -> dict:
    from services.amadeus_client import AmadeusClient
    settings = get_settings()

    if not settings.amadeus_client_id:
        return {"error": "Amadeus credentials not configured. Set AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET in .env"}

    client = AmadeusClient(settings.amadeus_client_id, settings.amadeus_client_secret)
    try:
        results = await client.search_flights(
            origin=args["origin"],
            destination=args["destination"],
            depart_date=args["depart_date"],
            return_date=args.get("return_date"),
            num_adults=args.get("num_adults", 2),
            max_results=args.get("max_results", 5),
        )
        # Deduplicate: sandbox sometimes returns identical flights
        seen: set[tuple] = set()
        unique = []
        for r in results:
            key = (r.airline, r.stops, r.depart_time[:13], round(r.price_usd, -1))
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return {"flights": [r.model_dump() for r in unique]}
    finally:
        await client.aclose()


async def _tool_search_hotels(args: dict, cl_msg) -> dict:
    settings = get_settings()

    if settings.hotel_provider == "expedia" and settings.expedia_eps_client_id:
        from services.expedia_rapid_client import ExpediaRapidClient
        client = ExpediaRapidClient(settings.expedia_eps_client_id, settings.expedia_eps_client_secret)
        provider_name = "Expedia Rapid API"
    else:
        if not settings.amadeus_client_id:
            return {"error": "Amadeus credentials not configured. Set AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET in .env"}
        from services.amadeus_client import AmadeusClient
        client = AmadeusClient(settings.amadeus_client_id, settings.amadeus_client_secret)
        provider_name = "Amadeus"

    try:
        results = await client.search_hotels(
            city_code=args["city_code"],
            check_in=args["check_in"],
            check_out=args["check_out"],
            num_adults=args.get("num_adults", 2),
            max_results=args.get("max_results", 5),
        )
        return {"hotels": [r.model_dump() for r in results], "provider": provider_name}
    finally:
        await client.aclose()


async def _tool_get_weather(args: dict) -> dict:
    from services.weather_client import WeatherClient
    client = WeatherClient()
    try:
        weather = await client.get_weather(city=args["city"], date=args["date"])
        return weather.model_dump()
    finally:
        await client.aclose()





async def _tool_search_hotel_details(args: dict) -> dict:
    """Use web search to get website, description, and image for all hotels in one call.

    Supports Anthropic (web_search_20250305) and OpenAI (web_search_preview via Responses API).
    Falls back to Google search URLs if neither is available.
    """
    import re

    hotel_names = args["hotel_names"][:3]  # Cap at 3 to limit web search costs
    city = args["city"]
    settings = get_settings()
    active_model = _get_active_model()

    _SEARCH_PROMPT = (
        f"Search for these hotels in {city}. "
        f"Return ONLY a JSON array with name, website_url, description (1 sentence):\n"
        + "\n".join(f"- {name}" for name in hotel_names)
        + f'\n\n[{{"name":"...","website_url":"https://...","description":"..."}}]'
    )

    # --- Anthropic path ---
    if "claude" in active_model.lower() and settings.anthropic_api_key:
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            response = await client.messages.create(
                model=active_model,
                max_tokens=800,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
                messages=[{"role": "user", "content": _SEARCH_PROMPT}],
            )
            text_content = next(
                (block.text for block in response.content if hasattr(block, "text")), ""
            )
            text_content = re.sub(r"```(?:json)?\s*|\s*```", "", text_content).strip()
            match = re.search(r"\[.*\]", text_content, re.DOTALL)
            if match:
                hotels = json.loads(match.group())
                return {"hotels": hotels}
        except Exception as exc:
            logger.warning("[search_hotel_details] Anthropic path failed: %s", exc)

    # --- OpenAI path (Responses API with web_search_preview) ---
    if "gpt" in active_model.lower() and settings.openai_api_key:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.openai_api_key)
            response = await client.responses.create(
                model=active_model,
                tools=[{"type": "web_search_preview"}],
                input=_SEARCH_PROMPT,
            )
            # Extract text from the message output item
            text_content = ""
            for item in response.output:
                if item.type == "message":
                    for block in item.content:
                        if block.type == "output_text":
                            text_content = block.text
                            break
            text_content = re.sub(r"```(?:json)?\s*|\s*```", "", text_content).strip()
            match = re.search(r"\[.*\]", text_content, re.DOTALL)
            if match:
                hotels = json.loads(match.group())
                return {"hotels": hotels}
        except Exception as exc:
            logger.warning("[search_hotel_details] OpenAI path failed: %s", exc)

    # --- Fallback: static Google search URLs ---
    return {
        "hotels": [
            {
                "name": name,
                "website_url": (
                    f"https://www.google.com/search?q="
                    f"{name.replace(' ', '+')}+{city.replace(' ', '+')}+official+site"
                ),
                "description": "",
            }
            for name in hotel_names
        ]
    }


def _tool_calculate_budget(args: dict) -> dict:
    flight = float(args["flight_price_usd"])
    hotel_nightly = float(args["hotel_price_per_night_usd"])
    nights = int(args["num_nights"])
    people = int(args["num_people"])
    daily = float(args["daily_expenses_usd"])
    budget = args.get("budget_usd")

    hotel_total = hotel_nightly * nights
    expenses_total = daily * nights * people
    total = flight + hotel_total + expenses_total

    breakdown = {
        "flights": round(flight, 2),
        "hotel": round(hotel_total, 2),
        "daily_expenses": round(expenses_total, 2),
    }

    within_budget = None
    if budget:
        within_budget = total <= float(budget)

    return {
        "total_usd": round(total, 2),
        "breakdown": breakdown,
        "within_budget": within_budget,
        "budget_usd": budget,
    }


async def _tool_format_itinerary(args: dict) -> dict:
    writer_model = get_settings().writer_model
    logger.info("[format_itinerary] Using writer model: %s", writer_model)
    response = await litellm.acompletion(
        model=writer_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an enthusiastic luxury travel writer. Produce a rich, beautifully formatted "
                    "markdown itinerary. Use emojis generously. Structure it exactly as follows:\n\n"
                    "# ✈️ [Destination] Getaway — [dates]\n"
                    "A warm 2-sentence intro teasing the trip.\n\n"
                    "## ✈️ Available Flights\n"
                    "Render ALL flights from the `flights` array as a markdown table:\n"
                    "| | Airline | Departs | Arrives | Duration | Stops | Price/person |\n"
                    "|---|---|---|---|---|---|---|\n"
                    "Mark the recommended flight (index from `recommended_flight_index`) with ⭐ **Bold** in the first column. "
                    "All other flights get a plain `·`. Show total price for all travelers in parentheses.\n\n"
                    "## 🏨 Where to Stay\n"
                    "For EACH hotel in the `hotels` list:\n"
                    "  - **[🏨 Hotel Name](website_url)** ⭐⭐⭐ (star count matching rating)\n"
                    "  - 💰 $X/night · 📅 X nights = $total\n"
                    "  - One vivid sentence description\n"
                    "  - Amenities as emoji bullets (🏊 Pool · 🍳 Breakfast · 🅿️ Parking etc.)\n\n"
                    "## 🌤️ Weather\n"
                    "Temperature range, conditions, 3 packing tips with emojis.\n\n"
                    "## 💰 Budget Breakdown\n"
                    "Markdown table: Category | Cost. End with **Total** row and ✅ within budget or ⚠️ over with note.\n\n"
                    "## 💡 Local Tips\n"
                    "5 specific, actionable tips with relevant emojis.\n\n"
                    "Write with excitement and warmth. Every section should make the reader want to book immediately."
                ),
            },
            {
                "role": "user",
                "content": f"Format this trip data into a complete itinerary:\n{json.dumps(args, indent=2)}",
            },
        ],
    )
    return {"markdown": response.choices[0].message.content}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

TOOL_MAP = {
    "parse_travel_request": _tool_parse_travel_request,
    "search_flights": _tool_search_flights,
    "search_hotels": _tool_search_hotels,
    "get_weather": _tool_get_weather,
    "search_hotel_details": _tool_search_hotel_details,
    "calculate_budget": _tool_calculate_budget,
    "format_itinerary": _tool_format_itinerary,
}


async def execute_tool_call(tool_call: Any, cl_msg) -> dict:
    """Execute a single tool call, wrapped in a Chainlit Step for visibility."""
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)

    async with cl.Step(name=f"🔧 {name}", type="tool", parent_id=cl_msg.id) as step:
        step.input = json.dumps(args, indent=2)
        try:
            fn = TOOL_MAP.get(name)
            if fn is None:
                result = {"error": f"Unknown tool: {name}"}
            elif name in ("search_flights", "search_hotels"):
                result = await fn(args, cl_msg)
            elif name in ("parse_travel_request", "get_weather", "search_hotel_details", "format_itinerary"):
                result = await fn(args)
            else:
                result = fn(args)  # sync tools

            step.output = json.dumps(result, indent=2)
            return {
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": name,
                "content": json.dumps(result),
            }
        except Exception as exc:
            error_result = {"error": str(exc)}
            step.output = json.dumps(error_result)
            return {
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": name,
                "content": json.dumps(error_result),
            }


async def execute_tool_calls_parallel(tool_calls: list[Any], cl_msg) -> list[dict]:
    """Execute all tool calls in parallel (asyncio.gather)."""
    tasks = [execute_tool_call(tc, cl_msg) for tc in tool_calls]
    return await asyncio.gather(*tasks)
