"""Public, provider-agnostic geocoding resources for application clients."""

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from services.geocoding import lookup_italian_addresses, reverse_geocode, search_locations

router = APIRouter()


class AddressSuggestionResponse(BaseModel):
    label: str
    road: str
    city: str
    province: str
    postalCode: str
    lat: float | None
    lng: float | None


class LocationResponse(BaseModel):
    lat: float
    lng: float
    label: str


class ReverseLocationResponse(BaseModel):
    label: str


@router.get("/addresses", response_model=list[AddressSuggestionResponse])
def list_address_suggestions(query: str = Query(..., max_length=200)) -> list[AddressSuggestionResponse]:
    """Return Italian address suggestions for address forms."""
    return [
        AddressSuggestionResponse(
            label=result.label,
            road=result.road,
            city=result.city,
            province=result.province,
            postalCode=result.postal_code,
            lat=result.lat,
            lng=result.lng,
        )
        for result in lookup_italian_addresses(query)
    ]


@router.get("/locations", response_model=list[LocationResponse])
def list_locations(query: str = Query(..., max_length=200)) -> list[LocationResponse]:
    """Return selectable locations for guest location discovery."""
    return [LocationResponse(lat=result.lat, lng=result.lng, label=result.label) for result in search_locations(query)]


@router.get("/locations/reverse", response_model=ReverseLocationResponse)
def get_reverse_location(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
) -> ReverseLocationResponse:
    """Return the human-readable location for geographic coordinates."""
    return ReverseLocationResponse(label=reverse_geocode(lat, lng))
