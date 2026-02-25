"""Tool schemas and dispatch layer for TravelMate.

Each tool:
  - Has an OpenAI-compatible JSON schema (works via LiteLLM for Claude + OpenAI)
  - Has an async implementation function
  - Runs inside a Chainlit Step for transparent step visibility
"""

from __future__ import annotations

import json
import asyncio
from typing import Any

import chainlit as cl
import litellm

from config.settings import get_settings
from models.travel_models import TravelRequest


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "parse_travel_request",
            "description": (
                "Extract structured travel parameters from a natural-language user request. "
                "Call this FIRST before any search tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_message": {
                        "type": "string",
                        "description": "The raw user travel request in natural language.",
                    }
                },
                "required": ["user_message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_flights",
            "description": "Search for available flights using Amadeus sandbox data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "IATA airport code (e.g. JFK)"},
                    "destination": {"type": "string", "description": "IATA airport code (e.g. BCN)"},
                    "depart_date": {"type": "string", "description": "Departure date YYYY-MM-DD"},
                    "return_date": {
                        "type": "string",
                        "description": "Return date YYYY-MM-DD (null for one-way)",
                    },
                    "num_adults": {"type": "integer", "description": "Number of adult travelers"},
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum flight offers to return",
                        "default": 5,
                    },
                },
                "required": ["origin", "destination", "depart_date", "num_adults"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotels",
            "description": "Search for hotels using Amadeus sandbox (or Expedia Rapid if configured).",
            "parameters": {
                "type": "object",
                "properties": {
                    "city_code": {
                        "type": "string",
                        "description": "IATA city code (e.g. BCN for Barcelona)",
                    },
                    "check_in": {"type": "string", "description": "Check-in date YYYY-MM-DD"},
                    "check_out": {"type": "string", "description": "Check-out date YYYY-MM-DD"},
                    "num_adults": {"type": "integer", "description": "Number of guests"},
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum hotel results",
                        "default": 5,
                    },
                },
                "required": ["city_code", "check_in", "check_out", "num_adults"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather conditions for a city (wttr.in, no auth required).",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name (e.g. Barcelona)"},
                    "date": {
                        "type": "string",
                        "description": "Target date YYYY-MM-DD (used for context only)",
                    },
                },
                "required": ["city", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_budget",
            "description": "Calculate total trip cost and check against user budget.",
            "parameters": {
                "type": "object",
                "properties": {
                    "flight_price_usd": {"type": "number", "description": "Total round-trip flight cost"},
                    "hotel_price_per_night_usd": {"type": "number", "description": "Hotel nightly rate"},
                    "num_nights": {"type": "integer", "description": "Number of hotel nights"},
                    "num_people": {"type": "integer", "description": "Number of travelers"},
                    "daily_expenses_usd": {
                        "type": "number",
                        "description": "Estimated daily food/activities per person",
                    },
                    "budget_usd": {
                        "type": "number",
                        "description": "User's total budget (null if not specified)",
                    },
                },
                "required": [
                    "flight_price_usd",
                    "hotel_price_per_night_usd",
                    "num_nights",
                    "num_people",
                    "daily_expenses_usd",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotel_details",
            "description": (
                "Search the web to find the official website URL, a short description, "
                "and a hero image URL for a list of hotels — all in one call. "
                "Call this after search_hotels, passing all returned hotel names at once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hotel_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of hotel names to search for",
                    },
                    "city": {
                        "type": "string",
                        "description": "City where the hotels are located (e.g. Barcelona)",
                    },
                },
                "required": ["hotel_names", "city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "format_itinerary",
            "description": "Produce a polished markdown itinerary from all gathered trip data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {"type": "string"},
                    "dates": {
                        "type": "object",
                        "description": "{'depart': str, 'return': str, 'num_nights': int}",
                    },
                    "flight": {"type": "object", "description": "Best flight offer dict"},
                    "hotels": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of hotel offers merged with web-search details (name, price, stars, website_url, description, image_url)",
                    },
                    "weather": {"type": "object", "description": "Weather info dict"},
                    "budget_summary": {"type": "object", "description": "Budget breakdown dict"},
                    "tips": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Travel tips for the destination",
                    },
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
    settings = get_settings()
    today = datetime.date.today().isoformat()

    response = await litellm.acompletion(
        model=settings.llm_model,
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
        return {"flights": [r.model_dump() for r in results]}
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
    """Use Claude web search to get website, description, and image for all hotels in one call."""
    import re
    import anthropic

    hotel_names = args["hotel_names"]
    city = args["city"]
    settings = get_settings()

    is_claude = "claude" in settings.llm_model.lower()

    if is_claude and settings.anthropic_api_key:
        try:
            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            hotel_list = "\n".join(f"- {name}" for name in hotel_names)

            response = await client.messages.create(
                model=settings.llm_model,
                max_tokens=2048,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": len(hotel_names) + 2,
                }],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Search for each of these hotels in {city} and return ONLY a JSON array. "
                        f"For each hotel find: official website URL, a 2-sentence description "
                        f"highlighting key features and location, and a hero image URL "
                        f"(look for og:image meta tag or main property photo on the official site).\n\n"
                        f"Hotels:\n{hotel_list}\n\n"
                        f"Return ONLY a valid JSON array, no markdown, no explanation:\n"
                        f'[{{"name": "...", "website_url": "https://...", '
                        f'"description": "...", "image_url": "https://..."}}]'
                    ),
                }],
            )

            # Extract the final text block (after web search tool use blocks)
            text_content = next(
                (block.text for block in response.content if hasattr(block, "text")),
                "",
            )

            # Strip markdown code fences if present
            text_content = re.sub(r"```(?:json)?\s*|\s*```", "", text_content).strip()
            json_match = re.search(r"\[.*\]", text_content, re.DOTALL)
            if json_match:
                return {"hotels": json.loads(json_match.group())}

        except Exception:
            pass  # Fall through to Google search fallback

    # Fallback: construct Google search URLs (works for any model)
    return {
        "hotels": [
            {
                "name": name,
                "website_url": (
                    f"https://www.google.com/search?q="
                    f"{name.replace(' ', '+')}+{city.replace(' ', '+')}+official+site"
                ),
                "description": "",
                "image_url": "",
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
    settings = get_settings()
    response = await litellm.acompletion(
        model=settings.llm_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a travel writer. Produce a beautifully formatted markdown itinerary "
                    "using the provided trip data. Include these sections:\n\n"
                    "1. **Flight Details** — airline, price, duration, stops, times\n"
                    "2. **Accommodation Options** — for each hotel in the `hotels` list:\n"
                    "   - If `image_url` is present and non-empty: embed it as `![Hotel Name](image_url)`\n"
                    "   - Render the hotel name as a clickable link using `website_url`: `[Name](url)`\n"
                    "   - Show stars, nightly rate, description, and amenities\n"
                    "3. **Weather Forecast** — conditions, temperature, what to pack\n"
                    "4. **Budget Summary** — clear breakdown table\n"
                    "5. **Top Travel Tips** — 4-5 specific, actionable tips\n\n"
                    "Make it engaging and practical. Use emojis sparingly."
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
            elif name in ("search_flights", "search_hotels", "format_itinerary"):
                result = await fn(args, cl_msg)
            elif name in ("parse_travel_request", "get_weather", "search_hotel_details"):
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
