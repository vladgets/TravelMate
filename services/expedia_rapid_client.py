"""Expedia Rapid API (EPS) hotel client — drop-in HotelProvider swap.

Activate by setting HOTEL_PROVIDER=expedia in .env.
Requires partner credentials: EXPEDIA_EPS_CLIENT_ID + EXPEDIA_EPS_CLIENT_SECRET.

Architecture note: This implements the same HotelProvider interface as AmadeusClient,
so it can be swapped in at runtime without any changes to the orchestrator or tools.
This demonstrates provider-abstracted design — the same pattern Expedia's internal
teams use to swap between lodging inventory sources.
"""

from __future__ import annotations

import hashlib
import time

import httpx

from models.travel_models import HotelResult
from services.base_travel_provider import HotelProvider

RAPID_BASE = "https://test.ean.com"  # Expedia sandbox; prod: "https://api.ean.com"


class ExpediaRapidClient(HotelProvider):
    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.AsyncClient(timeout=20.0)

    def _auth_headers(self) -> dict:
        """
        Rapid API uses HMAC-SHA512 signature auth.
        Header: Authorization: EAN apikey={key},signature={sig},timestamp={ts}
        Signature = SHA512(apikey + secret + timestamp)
        """
        timestamp = str(int(time.time()))
        sig_input = self._client_id + self._client_secret + timestamp
        signature = hashlib.sha512(sig_input.encode()).hexdigest()
        return {
            "Authorization": (
                f"EAN apikey={self._client_id},"
                f"signature={signature},"
                f"timestamp={timestamp}"
            ),
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }

    async def search_hotels(
        self,
        city_code: str,
        check_in: str,
        check_out: str,
        num_adults: int,
        max_results: int,
    ) -> list[HotelResult]:
        headers = self._auth_headers()

        # Rapid API uses property availability endpoint
        params = {
            "destination": city_code,
            "checkin": check_in,
            "checkout": check_out,
            "adults": str(num_adults),
            "rooms": "1",
            "currency": "USD",
            "language": "en-US",
            "country_code": "US",
            "sort_type": "recommended",
            "limit": str(max_results),
        }

        resp = await self._http.get(
            f"{RAPID_BASE}/v3/properties/availability",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        results: list[HotelResult] = []
        for prop in data.get("properties", [])[:max_results]:
            try:
                rate = prop.get("rate_plans", [{}])[0]
                nightly = rate.get("price", {}).get("totals", {}).get("inclusive", {})
                price = float(nightly.get("requestCurrency", {}).get("value", 0))

                amenities = [
                    a.get("name", "") for a in prop.get("amenities", [])[:5]
                ]

                results.append(
                    HotelResult(
                        name=prop.get("name", "Unknown Hotel"),
                        stars=int(prop.get("star_rating", 0) or 0),
                        price_per_night_usd=price,
                        rating=float(prop.get("reviews", {}).get("rating", 0) or 0),
                        amenities=amenities,
                        hotel_id=str(prop.get("property_id", "")),
                    )
                )
            except (KeyError, ValueError, IndexError):
                continue

        return results

    async def aclose(self):
        await self._http.aclose()
