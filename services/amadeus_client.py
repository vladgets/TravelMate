"""Amadeus free sandbox client — implements both FlightProvider and HotelProvider.

Note on sandbox hotel coverage: Amadeus sandbox has very limited hotel data and
returns zero results for most cities (including Barcelona, Lisbon, etc.).
search_hotels() falls back to curated mock data when the sandbox returns nothing,
so demos work regardless of sandbox coverage gaps.
"""

from __future__ import annotations

import time
import httpx

from models.travel_models import FlightResult, HotelResult
from services.base_travel_provider import FlightProvider, HotelProvider

# Curated mock hotels keyed by IATA city code.
# Used as fallback when Amadeus sandbox returns zero results.
_MOCK_HOTELS: dict[str, list[dict]] = {
    "BCN": [
        {"name": "Hotel Arts Barcelona", "stars": 5, "price_per_night_usd": 420, "rating": 4.8, "amenities": ["Pool", "Spa", "Sea view", "Michelin restaurant", "Gym"]},
        {"name": "Barceló Raval", "stars": 4, "price_per_night_usd": 195, "rating": 4.3, "amenities": ["Rooftop terrace", "Bar", "City view", "Free WiFi"]},
        {"name": "Hotel Catalonia Born", "stars": 4, "price_per_night_usd": 165, "rating": 4.2, "amenities": ["Free WiFi", "Bar", "24h reception", "Near La Boqueria"]},
        {"name": "Praktik Rambla", "stars": 3, "price_per_night_usd": 110, "rating": 4.0, "amenities": ["Free WiFi", "Central location", "Breakfast available"]},
        {"name": "Generator Barcelona", "stars": 3, "price_per_night_usd": 85, "rating": 3.8, "amenities": ["Bar", "Terrace", "Free WiFi", "Near Sagrada Família"]},
    ],
    "LIS": [
        {"name": "Bairro Alto Hotel", "stars": 5, "price_per_night_usd": 380, "rating": 4.9, "amenities": ["Rooftop bar", "Spa", "City view", "Restaurant"]},
        {"name": "Hotel do Chiado", "stars": 4, "price_per_night_usd": 210, "rating": 4.5, "amenities": ["Free WiFi", "Bar", "City center", "Breakfast"]},
        {"name": "Lisboa Carmo Hotel", "stars": 4, "price_per_night_usd": 175, "rating": 4.3, "amenities": ["Free WiFi", "Terrace", "Near tram 28"]},
        {"name": "My Story Hotel Rossio", "stars": 3, "price_per_night_usd": 120, "rating": 4.1, "amenities": ["Free WiFi", "Central location", "24h reception"]},
    ],
    "PAR": [
        {"name": "Le Bristol Paris", "stars": 5, "price_per_night_usd": 950, "rating": 4.9, "amenities": ["Pool", "Spa", "3 restaurants", "Garden", "Butler service"]},
        {"name": "Hotel du Louvre", "stars": 4, "price_per_night_usd": 340, "rating": 4.5, "amenities": ["Free WiFi", "Bar", "Louvre view", "Concierge"]},
        {"name": "Hotel Fabric", "stars": 4, "price_per_night_usd": 220, "rating": 4.4, "amenities": ["Free WiFi", "Bar", "Oberkampf district"]},
        {"name": "Generator Paris", "stars": 3, "price_per_night_usd": 95, "rating": 3.9, "amenities": ["Bar", "Free WiFi", "Near canal St-Martin"]},
    ],
    "ROM": [
        {"name": "Hotel Eden Roma", "stars": 5, "price_per_night_usd": 680, "rating": 4.8, "amenities": ["Rooftop pool", "Spa", "Via Veneto", "Fine dining"]},
        {"name": "Hotel Nazionale", "stars": 4, "price_per_night_usd": 290, "rating": 4.4, "amenities": ["Free WiFi", "Bar", "Near Pantheon"]},
        {"name": "The Beehive", "stars": 3, "price_per_night_usd": 130, "rating": 4.2, "amenities": ["Garden", "Organic café", "Free WiFi", "Near Termini"]},
    ],
    "AMS": [
        {"name": "Pulitzer Amsterdam", "stars": 5, "price_per_night_usd": 480, "rating": 4.7, "amenities": ["Canal view", "Bar", "Garden", "Bikes available"]},
        {"name": "Hotel V Nesplein", "stars": 4, "price_per_night_usd": 215, "rating": 4.4, "amenities": ["Free WiFi", "Bar", "Jordaan district"]},
        {"name": "Stayokay Amsterdam Vondelpark", "stars": 3, "price_per_night_usd": 105, "rating": 4.0, "amenities": ["Free WiFi", "Bar", "Vondelpark location"]},
    ],
}

# Generic fallback for cities not in the mock dict
_GENERIC_HOTELS = [
    {"name": "Grand City Hotel", "stars": 4, "price_per_night_usd": 220, "rating": 4.3, "amenities": ["Free WiFi", "Bar", "Gym", "Breakfast"]},
    {"name": "Boutique Central Hotel", "stars": 4, "price_per_night_usd": 175, "rating": 4.2, "amenities": ["Free WiFi", "City center", "24h reception"]},
    {"name": "Comfort Inn Downtown", "stars": 3, "price_per_night_usd": 130, "rating": 4.0, "amenities": ["Free WiFi", "Breakfast", "Parking"]},
    {"name": "Budget Stay Hotel", "stars": 3, "price_per_night_usd": 95, "rating": 3.8, "amenities": ["Free WiFi", "24h reception"]},
]

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
            return _mock_hotels_for(city_code, max_results)

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

        # Sandbox returned hotel IDs but no offers — fall back to mock data
        if not results:
            return _mock_hotels_for(city_code, max_results)

        return results

    async def aclose(self):
        await self._http.aclose()


def _mock_hotels_for(city_code: str, max_results: int) -> list[HotelResult]:
    """Return curated mock hotels for cities not covered by Amadeus sandbox."""
    raw = _MOCK_HOTELS.get(city_code.upper(), _GENERIC_HOTELS)
    return [
        HotelResult(
            name=h["name"],
            stars=h["stars"],
            price_per_night_usd=h["price_per_night_usd"],
            rating=h["rating"],
            amenities=h["amenities"],
            hotel_id=f"MOCK_{city_code.upper()}_{i}",
        )
        for i, h in enumerate(raw[:max_results])
    ]


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
