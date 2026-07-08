from fastapi import APIRouter, Depends

from backend.care_navigation.context import LocationContext
from backend.care_navigation.service import CareNavigationService
from backend.db.models import User
from backend.infra.auth import get_current_user
from backend.schemas.care_navigation import (
    CareNavigationRequest,
    CareNavigationResponse,
)

router = APIRouter(tags=["care-navigation"])
service = CareNavigationService()


@router.get("/care-navigation/status")
async def care_navigation_status(_: User = Depends(get_current_user)):
    return {
        "map_provider": "amap_mcp",
        "map_mcp_configured": service.map_client.configured,
        "web_verification_provider": "brave_search",
        "web_verification_configured": service.verifier.configured,
        "location_consent_required": True,
        "precise_location_persisted": False,
    }


@router.post(
    "/care-navigation/search",
    response_model=CareNavigationResponse,
)
async def search_nearby_care(
    request: CareNavigationRequest,
    _: User = Depends(get_current_user),
):
    location = LocationContext.from_value(request.location.model_dump())
    result = await service.search_async(
        request.medical_need,
        location,
        request.radius_meters,
    )
    return CareNavigationResponse(**result)
