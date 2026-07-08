"""High-level client for AMap's official Streamable HTTP MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
import os
from typing import Any
from urllib.parse import urlencode

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class MapProviderNotConfigured(RuntimeError):
    pass


class MapProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class MCPToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class MCPToolResult:
    tool_name: str
    structured: Any
    text: str
    is_error: bool


class AmapMCPClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("AMAP_MAPS_API_KEY", "").strip()
        self.base_url = os.getenv(
            "AMAP_MCP_URL",
            "https://mcp.amap.com/mcp",
        ).strip()
        self.timeout = float(os.getenv("AMAP_MCP_TIMEOUT_SECONDS", "20"))

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def _url(self) -> str:
        if not self.configured:
            raise MapProviderNotConfigured(
                "未配置 AMAP_MAPS_API_KEY，无法调用高德地图 MCP。"
            )
        separator = "&" if "?" in self.base_url else "?"
        return f"{self.base_url}{separator}{urlencode({'key': self.api_key})}"

    async def list_tools(self) -> list[MCPToolSpec]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http_client:
                async with streamable_http_client(
                    self._url(),
                    http_client=http_client,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        response = await session.list_tools()
                        return [
                            MCPToolSpec(
                                name=tool.name,
                                description=tool.description or "",
                                input_schema=dict(tool.inputSchema or {}),
                            )
                            for tool in response.tools
                        ]
        except MapProviderNotConfigured:
            raise
        except Exception as exc:
            raise MapProviderError(
                f"高德地图 MCP 工具发现失败: {self._safe_error(exc)}"
            ) from exc

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http_client:
                async with streamable_http_client(
                    self._url(),
                    http_client=http_client,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            name,
                            arguments=arguments,
                            read_timeout_seconds=timedelta(seconds=self.timeout),
                        )
        except MapProviderNotConfigured:
            raise
        except Exception as exc:
            raise MapProviderError(
                f"高德地图 MCP 调用失败: {self._safe_error(exc)}"
            ) from exc

        texts = [
            str(getattr(item, "text", ""))
            for item in (result.content or [])
            if getattr(item, "text", None)
        ]
        structured = getattr(result, "structuredContent", None)
        if structured is None:
            structured = _parse_json_text("\n".join(texts))
        return MCPToolResult(
            tool_name=name,
            structured=structured,
            text="\n".join(texts),
            is_error=bool(getattr(result, "isError", False)),
        )

    async def search_around(
        self,
        *,
        longitude: float,
        latitude: float,
        keyword: str,
        radius_meters: int,
    ) -> MCPToolResult:
        tools = await self.list_tools()
        tool = self._select_around_tool(tools)
        arguments = _build_around_arguments(
            tool.input_schema,
            longitude=longitude,
            latitude=latitude,
            keyword=keyword,
            radius_meters=radius_meters,
        )
        return await self.call_tool(tool.name, arguments)

    def _safe_error(self, exc: Exception) -> str:
        message = str(exc)
        if self.api_key:
            message = message.replace(self.api_key, "[REDACTED]")
        return message[:500]

    @staticmethod
    def _select_around_tool(tools: list[MCPToolSpec]) -> MCPToolSpec:
        explicit = os.getenv("AMAP_MCP_AROUND_TOOL", "").strip()
        if explicit:
            match = next((tool for tool in tools if tool.name == explicit), None)
            if match:
                return match
            raise MapProviderError(f"配置的 MCP 周边搜索工具不存在: {explicit}")

        def score(tool: MCPToolSpec) -> int:
            haystack = f"{tool.name} {tool.description}".lower()
            properties = set((tool.input_schema.get("properties") or {}).keys())
            value = 0
            if any(term in haystack for term in ("around", "nearby", "周边", "附近")):
                value += 10
            if "location" in properties:
                value += 4
            if properties & {"keywords", "keyword", "query"}:
                value += 3
            if any(term in haystack for term in ("search", "搜索")):
                value += 2
            return value

        ranked = sorted(tools, key=score, reverse=True)
        if not ranked or score(ranked[0]) < 10:
            available = ", ".join(tool.name for tool in tools)
            raise MapProviderError(f"未发现高德周边搜索工具，可用工具: {available}")
        return ranked[0]


def _parse_json_text(text: str) -> Any:
    value = (text or "").strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        start = min(
            (idx for idx in (value.find("{"), value.find("[")) if idx >= 0),
            default=-1,
        )
        if start >= 0:
            try:
                return json.loads(value[start:])
            except json.JSONDecodeError:
                pass
    return {"text": value}


def _build_around_arguments(
    schema: dict[str, Any],
    *,
    longitude: float,
    latitude: float,
    keyword: str,
    radius_meters: int,
) -> dict[str, Any]:
    properties = schema.get("properties") or {}
    location = f"{longitude:.6f},{latitude:.6f}"
    aliases: dict[str, Any] = {
        "location": location,
        "center": location,
        "origin": location,
        "keywords": keyword,
        "keyword": keyword,
        "query": keyword,
        "radius": radius_meters,
        "radius_meters": radius_meters,
        "types": "医疗保健服务",
        "type": "医疗保健服务",
    }
    arguments = {
        name: _coerce_schema_value(aliases[name], properties.get(name) or {})
        for name in properties
        if name in aliases
    }
    required = schema.get("required") or []
    missing = [name for name in required if name not in arguments]
    if missing:
        raise MapProviderError(
            f"周边搜索工具存在未适配的必填参数: {', '.join(missing)}"
        )
    return arguments


def _coerce_schema_value(value: Any, property_schema: dict[str, Any]) -> Any:
    value_type = property_schema.get("type")
    if value_type == "string":
        return str(value)
    if value_type == "integer":
        return int(value)
    if value_type == "number":
        return float(value)
    return value
