from __future__ import annotations

import asyncio
import json

from backend.care_navigation.context import (
    LocationContext,
    get_location_context,
    use_location_context,
)
from backend.care_navigation.mcp_client import (
    AmapMCPClient,
    MCPToolResult,
    MCPToolSpec,
    _build_around_arguments,
)
from backend.care_navigation.service import (
    CareNavigationService,
    haversine_meters,
    normalize_hospitals,
)
from backend.care_navigation.triage import (
    assess_care_navigation_need,
    recommend_department,
)
from backend.schemas.care_navigation import RequestLocation
import backend.tools.care_navigation as care_tool_module


def test_navigation_triage_and_department_are_deterministic():
    assessment = assess_care_navigation_need("咳嗽一周，附近医院挂什么科")

    assert assessment["care_navigation_required"] is True
    assert assessment["recommended_department"] == "呼吸内科"
    assert recommend_department("突然胸痛并且呼吸困难") == "急诊科"


def test_location_context_is_request_scoped_and_resets():
    assert get_location_context().usable is False

    with use_location_context(
        {
            "authorized": True,
            "latitude": 31.2304,
            "longitude": 121.4737,
            "accuracy_meters": 50,
        }
    ) as location:
        assert location.usable is True
        assert get_location_context().latitude == 31.2304

    assert get_location_context().usable is False


def test_authorized_location_requires_both_coordinates():
    try:
        RequestLocation(authorized=True, latitude=31.2)
    except ValueError as exc:
        assert "longitude" in str(exc)
    else:
        raise AssertionError("authorized location without longitude must fail")


def test_mcp_tool_discovery_prefers_around_search():
    tools = [
        MCPToolSpec(
            name="maps_text_search",
            description="关键词搜索",
            input_schema={"properties": {"keywords": {"type": "string"}}},
        ),
        MCPToolSpec(
            name="maps_around_search",
            description="周边搜索",
            input_schema={
                "properties": {
                    "location": {"type": "string"},
                    "keywords": {"type": "string"},
                    "radius": {"type": "integer"},
                }
            },
        ),
    ]

    selected = AmapMCPClient._select_around_tool(tools)
    arguments = _build_around_arguments(
        selected.input_schema,
        longitude=121.4737,
        latitude=31.2304,
        keyword="医院 呼吸内科",
        radius_meters=10000,
    )

    assert selected.name == "maps_around_search"
    assert arguments["location"] == "121.473700,31.230400"
    assert arguments["keywords"] == "医院 呼吸内科"
    assert arguments["radius"] == 10000


def test_mcp_errors_redact_api_key(monkeypatch):
    monkeypatch.setenv("AMAP_MAPS_API_KEY", "secret-map-key")
    client = AmapMCPClient()

    message = client._safe_error(
        RuntimeError("failed https://mcp.amap.com/mcp?key=secret-map-key")
    )

    assert "secret-map-key" not in message
    assert "[REDACTED]" in message


def test_hospital_normalization_computes_distance_and_filters_non_medical():
    payload = {
        "pois": [
            {
                "name": "测试市人民医院",
                "address": "健康路1号",
                "location": "121.474700,31.230400",
                "type": "医疗保健服务;综合医院",
            },
            {
                "name": "测试咖啡馆",
                "address": "健康路2号",
                "location": "121.475700,31.230400",
                "type": "餐饮服务",
            },
        ]
    }

    hospitals = normalize_hospitals(
        payload,
        user_latitude=31.2304,
        user_longitude=121.4737,
    )

    assert len(hospitals) == 1
    assert hospitals[0]["name"] == "测试市人民医院"
    assert 80 <= hospitals[0]["distance_meters"] <= 120
    assert hospitals[0]["map_uri"].startswith("https://uri.amap.com/marker")
    assert 80 <= haversine_meters(31.2304, 121.4737, 31.2304, 121.4747) <= 120


class FakeMapClient:
    configured = True

    def __init__(self):
        self.calls = []

    async def search_around(self, **kwargs):
        self.calls.append(kwargs)
        return MCPToolResult(
            tool_name="maps_around_search",
            structured={
                "pois": [
                    {
                        "name": "测试市中心医院",
                        "address": "中心路8号",
                        "location": "121.474700,31.230400",
                        "distance": "95",
                        "type": "医疗保健服务;综合医院",
                    }
                ]
            },
            text="",
            is_error=False,
        )


class FakeVerifier:
    configured = True

    def verify(self, hospital_name, department, city_hint=""):
        return {
            "status": "ok",
            "sources": [
                {
                    "title": f"{hospital_name}官网",
                    "url": "https://hospital.example/department",
                    "description": department,
                    "official_likely": True,
                }
            ],
        }


def test_service_never_calls_map_without_verified_consent():
    map_client = FakeMapClient()
    service = CareNavigationService(map_client=map_client, verifier=FakeVerifier())

    result = asyncio.run(
        service.search_async(
            "咳嗽应该去哪看",
            LocationContext(authorized=False),
        )
    )

    assert result["status"] == "location_consent_required"
    assert map_client.calls == []


def test_service_reports_missing_map_configuration(monkeypatch):
    monkeypatch.delenv("AMAP_MAPS_API_KEY", raising=False)
    service = CareNavigationService()
    location = LocationContext(
        authorized=True,
        latitude=31.2304,
        longitude=121.4737,
    )

    result = asyncio.run(service.search_async("附近医院挂什么科", location))

    assert result["status"] == "map_provider_not_configured"
    assert result["hospitals"] == []
    assert "AMAP_MAPS_API_KEY" in result["message"]


def test_service_returns_ranked_hospital_and_web_verification():
    map_client = FakeMapClient()
    service = CareNavigationService(map_client=map_client, verifier=FakeVerifier())
    location = LocationContext(
        authorized=True,
        latitude=31.2304,
        longitude=121.4737,
        accuracy_meters=30,
    )

    result = asyncio.run(service.search_async("咳嗽一周需要检查", location))

    assert result["status"] == "ok"
    assert result["recommended_department"] == "呼吸内科"
    assert result["hospital_count"] == 1
    assert result["hospitals"][0]["distance_meters"] == 95
    assert result["hospitals"][0]["web_verification"]["status"] == "ok"
    assert "location_used" not in result
    assert "user_location" not in result


def test_agent_tool_uses_server_context_not_model_arguments(monkeypatch):
    captured = {}
    traces = []

    class FakeToolService:
        def search(self, medical_need, location, radius_meters):
            captured["location"] = location
            return {
                "status": "location_consent_required"
                if not location.usable
                else "ok",
                "recommended_department": "呼吸内科",
                "urgency": "routine",
                "hospitals": [],
            }

    monkeypatch.setattr(care_tool_module, "service", FakeToolService())
    monkeypatch.setattr(care_tool_module, "record_rag_context", traces.append)

    no_consent = json.loads(
        care_tool_module.search_nearby_medical_care.invoke(
            {"medical_need": "咳嗽需要检查", "radius_meters": 5000}
        )
    )
    assert no_consent["status"] == "location_consent_required"
    assert captured["location"].usable is False

    with use_location_context(
        {
            "authorized": True,
            "latitude": 31.2304,
            "longitude": 121.4737,
        }
    ):
        authorized = json.loads(
            care_tool_module.search_nearby_medical_care.invoke(
                {"medical_need": "咳嗽需要检查", "radius_meters": 5000}
            )
        )
        second_call = care_tool_module.search_nearby_medical_care.invoke(
            {"medical_need": "咳嗽需要检查", "radius_meters": 5000}
        )
    assert authorized["status"] == "ok"
    assert second_call.startswith("TOOL_CALL_LIMIT_REACHED")
    assert traces[-1]["location_authorized"] is True
    assert "latitude" not in traces[-1]
    assert "longitude" not in traces[-1]
