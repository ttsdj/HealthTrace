"""Agent tool for location-authorized nearby medical care search."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from backend.care_navigation.context import (
    get_location_context,
    try_acquire_care_navigation_call,
)
from backend.care_navigation.service import CareNavigationService
from backend.chat.rag_context import record_rag_context

service = CareNavigationService()


@tool
def search_nearby_medical_care(
    medical_need: str,
    radius_meters: int = 10000,
) -> str:
    """Search nearby hospitals after verified user location consent.

    Use this when the user explicitly asks where to seek care, needs an
    in-person examination, or available evidence is insufficient for an online
    conclusion. Never use it to delay emergency advice.
    """

    location = get_location_context()
    if location.usable and not try_acquire_care_navigation_call():
        return (
            "TOOL_CALL_LIMIT_REACHED: nearby care navigation has already been "
            "called once in this turn. Use the existing result."
        )
    result = service.search(
        medical_need=medical_need,
        location=location,
        radius_meters=radius_meters,
    )
    record_rag_context(
        {
            "tool_used": True,
            "tool_name": "search_nearby_medical_care",
            "care_navigation_status": result.get("status"),
            "care_navigation_result_count": len(result.get("hospitals") or []),
            "recommended_department": result.get("recommended_department"),
            "location_authorized": location.usable,
            "search_radius_meters": result.get("search_radius_meters"),
            "urgency": result.get("urgency"),
        }
    )
    return json.dumps(result, ensure_ascii=False)
