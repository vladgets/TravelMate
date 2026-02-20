from __future__ import annotations

from pydantic import BaseModel, Field


class TravelRequest(BaseModel):
    origin: str = Field(description="IATA airport code for departure city")
    destination: str = Field(description="IATA airport code for destination city")
    city_code: str = Field(description="IATA city code for hotel search (often same as destination)")
    depart_date: str = Field(description="Departure date in YYYY-MM-DD format")
    return_date: str | None = Field(default=None, description="Return date in YYYY-MM-DD format")
    num_adults: int = Field(default=2, ge=1, description="Number of adult travelers")
    budget_usd: float | None = Field(default=None, description="Total budget in USD")
    preferences: list[str] = Field(default_factory=list, description="User preferences and interests")


class FlightResult(BaseModel):
    airline: str
    price_usd: float
    duration_hours: float
    stops: int
    depart_time: str
    arrive_time: str
    flight_number: str = ""


class HotelResult(BaseModel):
    name: str
    stars: int = 0
    price_per_night_usd: float
    rating: float = 0.0
    amenities: list[str] = Field(default_factory=list)
    hotel_id: str = ""


class WeatherInfo(BaseModel):
    condition: str
    temp_c: float
    temp_f: float
    description: str


class BudgetSummary(BaseModel):
    flight_cost_usd: float
    hotel_cost_usd: float
    daily_expenses_usd: float
    total_usd: float
    budget_usd: float | None = None
    within_budget: bool | None = None
    breakdown: dict = Field(default_factory=dict)


class Itinerary(BaseModel):
    destination: str
    dates: dict
    flight: FlightResult | None = None
    hotel: HotelResult | None = None
    weather: WeatherInfo | None = None
    budget_summary: BudgetSummary | None = None
    tips: list[str] = Field(default_factory=list)
    markdown: str = ""
