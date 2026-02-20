from abc import ABC, abstractmethod

from models.travel_models import FlightResult, HotelResult


class FlightProvider(ABC):
    @abstractmethod
    async def search_flights(
        self,
        origin: str,
        destination: str,
        depart_date: str,
        return_date: str | None,
        num_adults: int,
        max_results: int,
    ) -> list[FlightResult]:
        """Search for available flights."""


class HotelProvider(ABC):
    @abstractmethod
    async def search_hotels(
        self,
        city_code: str,
        check_in: str,
        check_out: str,
        num_adults: int,
        max_results: int,
    ) -> list[HotelResult]:
        """Search for available hotels."""
