"""Care navigation orchestration and map result normalization."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from typing import Any, Coroutine
from urllib.parse import quote

from backend.care_navigation.context import LocationContext
from backend.care_navigation.mcp_client import (
    AmapMCPClient,
    MapProviderError,
    MapProviderNotConfigured,
)
from backend.care_navigation.triage import (
    assess_care_navigation_need,
    recommend_department,
)
from backend.care_navigation.web_search import BraveHospitalVerifier


class CareNavigationService:
    def __init__(
        self,
        map_client: AmapMCPClient | None = None,
        verifier: BraveHospitalVerifier | None = None,
    ) -> None:
        self.map_client = map_client or AmapMCPClient()
        self.verifier = verifier or BraveHospitalVerifier()

    async def search_async(
        self,
        medical_need: str,
        location: LocationContext,
        radius_meters: int | None = None,
    ) -> dict:
        assessment = assess_care_navigation_need(medical_need)
        department = recommend_department(medical_need)
        if not location.usable:
            return {
                "status": "location_consent_required",
                **assessment,
                "recommended_department": department,
                "message": "需要用户点击定位按钮，明确授权本轮请求使用浏览器位置。",
                "hospitals": [],
            }

        radius = _bounded_radius(radius_meters)
        keyword = (
            f"急诊|医院|{department}"
            if assessment["urgency"] == "emergency"
            else f"医院|{department}"
        )
        try:
            result = await self.map_client.search_around(
                longitude=location.longitude,
                latitude=location.latitude,
                keyword=keyword,
                radius_meters=radius,
            )
            if result.is_error:
                raise MapProviderError(result.text or "地图 MCP 返回错误")
        except MapProviderNotConfigured as exc:
            return _provider_failure(
                "map_provider_not_configured",
                str(exc),
                assessment,
                department,
            )
        except MapProviderError as exc:
            return _provider_failure(
                "map_provider_error",
                str(exc),
                assessment,
                department,
            )

        hospitals = normalize_hospitals(
            result.structured if result.structured is not None else result.text,
            user_latitude=location.latitude,
            user_longitude=location.longitude,
        )
        limit = max(1, int(os.getenv("CARE_NAVIGATION_RESULT_LIMIT", "5")))
        hospitals = _rank_hospitals(hospitals, department)[:limit]

        verify_limit = max(
            0,
            int(os.getenv("CARE_NAVIGATION_WEB_VERIFY_LIMIT", "3")),
        )
        for hospital in hospitals[:verify_limit]:
            hospital["web_verification"] = self.verifier.verify(
                hospital["name"],
                department,
            )
        for hospital in hospitals[verify_limit:]:
            hospital["web_verification"] = {
                "status": "not_requested",
                "sources": [],
            }

        return {
            "status": "ok" if hospitals else "no_results",
            **assessment,
            "recommended_department": department,
            "search_radius_meters": radius,
            "map_provider": "amap_mcp",
            "map_tool": result.tool_name,
            "location_handling": "request_only_not_persisted",
            "hospital_count": len(hospitals),
            "hospitals": hospitals,
            "emergency_notice": (
                "检测到可能急危重症，请立即拨打 120 或前往最近急诊；"
                "以下地图结果不能替代急救。"
                if assessment["urgency"] == "emergency"
                else ""
            ),
            "ranking_notice": "结果按距离与科室关键词相关性排序，不代表医院医疗质量排名。",
        }

    def search(
        self,
        medical_need: str,
        location: LocationContext,
        radius_meters: int | None = None,
    ) -> dict:
        return run_async_compatible(
            self.search_async(medical_need, location, radius_meters)
        )


def _provider_failure(
    status: str,
    message: str,
    assessment: dict,
    department: str,
) -> dict:
    return {
        "status": status,
        **assessment,
        "recommended_department": department,
        "message": message,
        "hospitals": [],
    }


def _bounded_radius(value: int | None) -> int:
    default = int(os.getenv("CARE_NAVIGATION_RADIUS_METERS", "10000"))
    try:
        radius = int(value or default)
    except (TypeError, ValueError):
        radius = default
    return min(max(radius, 1000), 50000)


def run_async_compatible(coro: Coroutine[Any, Any, dict]) -> dict:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def normalize_hospitals(
    payload: Any,
    *,
    user_latitude: float,
    user_longitude: float,
) -> list[dict]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []

    candidates = _find_poi_dicts(payload)
    output: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        name = str(item.get("name") or item.get("title") or "").strip()
        address = _string_value(
            item.get("address")
            or item.get("formatted_address")
            or item.get("district")
        )
        location = _string_value(
            item.get("location")
            or item.get("coordinates")
            or item.get("lnglat")
        )
        if not name or not _looks_medical(item, name):
            continue
        key = (name, address)
        if key in seen:
            continue
        seen.add(key)
        lon, lat = _parse_location(location)
        distance = _optional_distance(
            item.get("distance")
            or item.get("distance_meters")
        )
        if distance is None and lon is not None and lat is not None:
            distance = round(
                haversine_meters(
                    user_latitude,
                    user_longitude,
                    lat,
                    lon,
                )
            )
        output.append(
            {
                "name": name,
                "address": address,
                "distance_meters": distance,
                "location": location,
                "telephone": _string_value(
                    item.get("tel")
                    or item.get("telephone")
                    or item.get("phone")
                ),
                "poi_type": _string_value(item.get("type") or item.get("category")),
                "map_source": "amap_mcp",
                "map_uri": _map_uri(name, lon, lat),
            }
        )
    return output


def _find_poi_dicts(value: Any) -> list[dict]:
    found: list[dict] = []
    if isinstance(value, dict):
        if (
            ("name" in value or "title" in value)
            and any(key in value for key in ("address", "location", "distance", "type"))
        ):
            found.append(value)
        for child in value.values():
            found.extend(_find_poi_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_find_poi_dicts(child))
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                found.extend(_find_poi_dicts(json.loads(text)))
            except json.JSONDecodeError:
                pass
    return found


def _looks_medical(item: dict, name: str) -> bool:
    text = f"{name} {_string_value(item.get('type') or item.get('category'))}"
    return any(
        term in text
        for term in ("医院", "急诊", "医疗", "卫生院", "诊所", "门诊部", "医务室")
    )


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(item) for item in value if item is not None)
    if isinstance(value, dict):
        if "lng" in value and "lat" in value:
            return f"{value['lng']},{value['lat']}"
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _parse_location(value: str) -> tuple[float | None, float | None]:
    try:
        left, right = value.split(",", 1)
        return float(left), float(right)
    except (AttributeError, TypeError, ValueError):
        return None, None


def _optional_distance(value: Any) -> int | None:
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def haversine_meters(
    latitude_1: float,
    longitude_1: float,
    latitude_2: float,
    longitude_2: float,
) -> float:
    radius = 6_371_000
    phi_1 = math.radians(latitude_1)
    phi_2 = math.radians(latitude_2)
    delta_phi = math.radians(latitude_2 - latitude_1)
    delta_lambda = math.radians(longitude_2 - longitude_1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_1) * math.cos(phi_2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _rank_hospitals(hospitals: list[dict], department: str) -> list[dict]:
    for hospital in hospitals:
        name_and_type = f"{hospital['name']} {hospital.get('poi_type', '')}"
        hospital["_department_match"] = department in name_and_type
    ranked = sorted(
        hospitals,
        key=lambda item: (
            not item["_department_match"],
            item["distance_meters"] is None,
            item["distance_meters"] or 10**9,
            item["name"],
        ),
    )
    for item in ranked:
        matched = item.pop("_department_match")
        item["ranking_reason"] = (
            "名称或POI类别包含建议科室，随后按距离排序"
            if matched
            else "按距离排序"
        )
    return ranked


def _map_uri(
    name: str,
    longitude: float | None,
    latitude: float | None,
) -> str:
    if longitude is None or latitude is None:
        return ""
    return (
        "https://uri.amap.com/marker?"
        f"position={longitude:.6f},{latitude:.6f}"
        f"&name={quote(name)}&src=medretrieve"
    )
