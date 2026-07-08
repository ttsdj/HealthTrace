"""Optional web verification for hospital official information."""

from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx


class BraveHospitalVerifier:
    def __init__(self) -> None:
        self.api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
        self.endpoint = os.getenv(
            "BRAVE_SEARCH_ENDPOINT",
            "https://api.search.brave.com/res/v1/web/search",
        ).strip()
        self.timeout = float(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "10"))

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.endpoint)

    def verify(self, hospital_name: str, department: str, city_hint: str = "") -> dict:
        if not self.configured:
            return {"status": "not_configured", "sources": []}
        query = " ".join(
            item
            for item in (hospital_name, department, city_hint, "门诊 官网")
            if item
        )
        try:
            response = httpx.get(
                self.endpoint,
                params={
                    "q": query,
                    "country": "CN",
                    "search_lang": "zh-hans",
                    "count": 5,
                    "safesearch": "strict",
                },
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return {
                "status": "error",
                "error": str(exc)[:300],
                "sources": [],
            }

        sources = []
        for item in ((payload.get("web") or {}).get("results") or [])[:5]:
            url = str(item.get("url") or "")
            title = str(item.get("title") or "")
            description = str(item.get("description") or "")
            domain = urlparse(url).netloc.lower()
            official_likely = (
                hospital_name[:4] in title
                and (
                    "官网" in title
                    or domain.endswith(".gov.cn")
                    or domain.endswith(".edu.cn")
                    or "hospital" in domain
                )
            )
            sources.append(
                {
                    "title": title,
                    "url": url,
                    "description": description[:400],
                    "domain": domain,
                    "official_likely": official_likely,
                }
            )
        return {
            "status": "ok" if sources else "no_results",
            "sources": sources,
        }
