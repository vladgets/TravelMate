"""Amadeus free sandbox client — implements both FlightProvider and HotelProvider."""

from __future__ import annotations

import time
import httpx

from models.travel_models import FlightResult, HotelResult
from services.base_travel_provider import FlightProvider, HotelProvider

AMADEUS_BASE = "https://test.api.amadeus.com"
TOKEN_URL = f"{AMADEUS_BASE}/v1/security/oauth2/token"


class AmadeusClient(FlightProvider, HotelProvider):
    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._http = httpx.AsyncClient(timeout=20.0)

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 30:
            return self._token

        resp = await self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 1799)
        return self._token

    async def _auth_headers(self) -> dict:
        token = await self._get_token()
        return {"Authorization": f"Bearer {token}"}

    async def search_flights(
        self,
        origin: str,
        destination: str,
        depart_date: str,
        return_date: str | None,
        num_adults: int,
        max_results: int,
    ) -> list[FlightResult]:
        headers = await self._auth_headers()
        params: dict = {
            "originLocationCode": origin.upper(),
            "destinationLocationCode": destination.upper(),
            "departureDate": depart_date,
            "adults": str(num_adults),
            "max": str(max_results),
            "currencyCode": "USD",
        }
        if return_date:
            params["returnDate"] = return_date

        resp = await self._http.get(
            f"{AMADEUS_BASE}/v2/shopping/flight-offers",
            headers=headers,
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        results: list[FlightResult] = []
        for offer in data.get("data", []):
            try:
                price = float(offer["price"]["grandTotal"])
                itineraries = offer.get("itineraries", [])
                if not itineraries:
                    continue
                first_it = itineraries[0]
                segments = first_it.get("segments", [])
                if not segments:
                    continue

                # Calculate total duration
                duration_str = first_it.get("duration", "PT0H")
                duration_hours = _parse_iso_duration(duration_str)

                first_seg = segments[0]
                last_seg = segments[-1]
                airline_code = first_seg.get("carrierCode", "?")
                flight_number = f"{airline_code}{first_seg.get('number', '')}"

                results.append(
                    FlightResult(
                        airline=airline_code,
                        price_usd=price,
                        duration_hours=duration_hours,
                        stops=len(segments) - 1,
                        depart_time=first_seg["departure"]["at"],
                        arrive_time=last_seg["arrival"]["at"],
                        flight_number=flight_number,
                    )
                )
            except (KeyError, ValueError):
                continue

        return results

    async def search_hotels(
        self,
        city_code: str,
        check_in: str,
        check_out: str,
        num_adults: int,
        max_results: int,
    ) -> list[HotelResult]:
        headers = await self._auth_headers()

        # Step 1: Get hotel IDs for city
        hotels_resp = await self._http.get(
            f"{AMADEUS_BASE}/v1/reference-data/locations/hotels/by-city",
            headers=headers,
            params={"cityCode": city_code.upper(), "radius": 5, "radiusUnit": "KM"},
        )
        hotels_resp.raise_for_status()
        hotel_ids = [
            h["hotelId"]
            for h in hotels_resp.json().get("data", [])[:max_results]
        ]

        if not hotel_ids:
            return []

        # Step 2: Get offers for those hotels
        offers_resp = await self._http.get(
            f"{AMADEUS_BASE}/v3/shopping/hotel-offers",
            headers=headers,
            params={
                "hotelIds": ",".join(hotel_ids),
                "checkInDate": check_in,
                "checkOutDate": check_out,
                "adults": str(num_adults),
                "currency": "USD",
                "bestRateOnly": "true",
            },
        )
        offers_resp.raise_for_status()
        data = offers_resp.json()

        results: list[HotelResult] = []
        for item in data.get("data", []):
            try:
                hotel_info = item.get("hotel", {})
                offers = item.get("offers", [])
                if not offers:
                    continue
                best_offer = offers[0]
                price = float(best_offer["price"]["total"])

                results.append(
                    HotelResult(
                        name=hotel_info.get("name", "Unknown Hotel"),
                        stars=int(hotel_info.get("rating", 0) or 0),
                        price_per_night_usd=price,
                        rating=float(hotel_info.get("rating", 0) or 0),
                        amenities=hotel_info.get("amenities", [])[:5],
                        hotel_id=hotel_info.get("hotelId", ""),
                    )
                )
            except (KeyError, ValueError):
                continue

        return results

    async def aclose(self):
        await self._http.aclose()


def _parse_iso_duration(duration: str) -> float:
    """Parse ISO 8601 duration like PT2H30M into fractional hours."""
    duration = duration.upper().lstrip("PT")
    hours = 0.0
    minutes = 0.0
    if "H" in duration:
        parts = duration.split("H")
        hours = float(parts[0])
        duration = parts[1]
    if "M" in duration:
        minutes = float(duration.replace("M", ""))
    return hours + minutes / 60
