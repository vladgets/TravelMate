"""wttr.in weather client — zero auth, instant setup."""

from __future__ import annotations

import httpx

from models.travel_models import WeatherInfo

WTTR_BASE = "https://wttr.in"


class WeatherClient:
    def __init__(self):
        self._http = httpx.AsyncClient(timeout=10.0)

    async def get_weather(self, city: str, date: str) -> WeatherInfo:
        """
        Fetch weather for city. wttr.in doesn't support future date lookup
        via the JSON API, so we return current/seasonal conditions.
        The city query returns current conditions with a 3-day forecast.
        """
        resp = await self._http.get(
            f"{WTTR_BASE}/{city}",
            params={"format": "j1"},
            headers={"User-Agent": "TravelMate/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()

        current = data["current_condition"][0]
        temp_c = float(current["temp_C"])
        temp_f = float(current["temp_F"])
        description = current["weatherDesc"][0]["value"]

        # Simple condition mapping
        condition = _classify_condition(description)

        return WeatherInfo(
            condition=condition,
            temp_c=temp_c,
            temp_f=temp_f,
            description=description,
        )

    async def aclose(self):
        await self._http.aclose()


def _classify_condition(description: str) -> str:
    desc_lower = description.lower()
    if any(w in desc_lower for w in ("sun", "clear", "bright")):
        return "sunny"
    if any(w in desc_lower for w in ("cloud", "overcast")):
        return "cloudy"
    if any(w in desc_lower for w in ("rain", "drizzle", "shower")):
        return "rainy"
    if any(w in desc_lower for w in ("snow", "blizzard", "sleet")):
        return "snowy"
    if "thunder" in desc_lower or "storm" in desc_lower:
        return "stormy"
    return "partly cloudy"
