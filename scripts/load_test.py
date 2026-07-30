"""Dependency-light staged HTTP load test for HealthTrace.

The script creates one isolated user, then exercises liveness, authenticated
database access, synchronous chat, and SSE chat.  It reports only locally
observed measurements and validates per-request markers to catch cross-talk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx


@dataclass
class Sample:
    elapsed_ms: float
    ok: bool
    status_code: int | None
    error: str = ""


@dataclass
class ScenarioResult:
    name: str
    requests: int
    concurrency: int
    successes: int
    failures: int
    wall_seconds: float
    throughput_rps: float
    latency_ms_min: float
    latency_ms_mean: float
    latency_ms_p50: float
    latency_ms_p95: float
    latency_ms_p99: float
    latency_ms_max: float
    errors: list[str]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


async def _run_scenario(
    name: str,
    requests: int,
    concurrency: int,
    operation,
) -> ScenarioResult:
    queue: asyncio.Queue[int] = asyncio.Queue()
    for index in range(requests):
        queue.put_nowait(index)
    samples: list[Sample] = []

    async def worker() -> None:
        while not queue.empty():
            try:
                index = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            started = time.perf_counter()
            try:
                status_code, valid, error = await operation(index)
                samples.append(
                    Sample(
                        elapsed_ms=(time.perf_counter() - started) * 1000,
                        ok=valid and 200 <= status_code < 300,
                        status_code=status_code,
                        error=error,
                    )
                )
            except Exception as exc:
                samples.append(
                    Sample(
                        elapsed_ms=(time.perf_counter() - started) * 1000,
                        ok=False,
                        status_code=None,
                        error=f"{type(exc).__name__}: {str(exc)[:180]}",
                    )
                )
            finally:
                queue.task_done()

    wall_started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(min(concurrency, requests))))
    wall_seconds = time.perf_counter() - wall_started
    latencies = [sample.elapsed_ms for sample in samples]
    errors: list[str] = []
    for sample in samples:
        if not sample.ok:
            item = sample.error or f"HTTP {sample.status_code}"
            if item not in errors:
                errors.append(item)
            if len(errors) >= 5:
                break
    successes = sum(sample.ok for sample in samples)
    return ScenarioResult(
        name=name,
        requests=requests,
        concurrency=concurrency,
        successes=successes,
        failures=len(samples) - successes,
        wall_seconds=round(wall_seconds, 4),
        throughput_rps=round(len(samples) / wall_seconds, 2) if wall_seconds else 0.0,
        latency_ms_min=round(min(latencies), 2) if latencies else 0.0,
        latency_ms_mean=round(statistics.fmean(latencies), 2) if latencies else 0.0,
        latency_ms_p50=round(_percentile(latencies, 0.50), 2),
        latency_ms_p95=round(_percentile(latencies, 0.95), 2),
        latency_ms_p99=round(_percentile(latencies, 0.99), 2),
        latency_ms_max=round(max(latencies), 2) if latencies else 0.0,
        errors=errors,
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--profile", choices=("smoke", "staged"), default="smoke")
    parser.add_argument(
        "--scenario",
        choices=("all", "live", "auth_me", "chat", "sse"),
        default="all",
    )
    parser.add_argument("--requests", type=int)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    profile = {
        "smoke": {
            "live": (10, 2),
            "auth_me": (10, 2),
            "chat": (5, 1),
            "sse": (5, 1),
        },
        "staged": {
            "live": (500, 100),
            "auth_me": (500, 100),
            "chat": (100, 20),
            "sse": (100, 20),
        },
    }[args.profile]

    timeout = httpx.Timeout(35.0, connect=5.0)
    limits = httpx.Limits(max_connections=160, max_keepalive_connections=120)
    async with httpx.AsyncClient(
        base_url=args.base_url,
        timeout=timeout,
        limits=limits,
    ) as client:
        username = f"loadtest_{uuid.uuid4().hex[:12]}"
        password = f"Load-{uuid.uuid4().hex}-9!"
        register = await client.post(
            "/auth/register",
            json={"username": username, "password": password, "role": "user"},
        )
        register.raise_for_status()
        token = register.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        async def live(_: int):
            response = await client.get("/health/live")
            return response.status_code, response.json().get("live") is True, ""

        async def auth_me(_: int):
            response = await client.get("/auth/me", headers=headers)
            valid = response.status_code == 200 and response.json().get("username") == username
            error = (
                ""
                if valid
                else f"HTTP {response.status_code}" if response.status_code != 200 else "auth identity mismatch"
            )
            return response.status_code, valid, error

        async def chat(index: int):
            session_id = f"sync-{index}-{uuid.uuid4().hex[:8]}"
            message = f"marker-{index}-{uuid.uuid4().hex[:8]}"
            response = await client.post(
                "/chat",
                headers=headers,
                json={"message": message, "session_id": session_id},
            )
            expected = f"stub:{username}:{session_id}:{message}"
            valid = response.status_code == 200 and response.json().get("response") == expected
            error = (
                ""
                if valid
                else f"HTTP {response.status_code}" if response.status_code != 200 else "sync marker mismatch"
            )
            return response.status_code, valid, error

        async def sse(index: int):
            session_id = f"sse-{index}-{uuid.uuid4().hex[:8]}"
            message = f"marker-{index}-{uuid.uuid4().hex[:8]}"
            response = await client.post(
                "/chat/stream",
                headers=headers,
                json={"message": message, "session_id": session_id},
            )
            expected = f"stub:{username}:{session_id}:{message}"
            valid = (
                response.status_code == 200
                and expected in response.text
                and "data: [DONE]" in response.text
            )
            error = (
                ""
                if valid
                else f"HTTP {response.status_code}" if response.status_code != 200 else "SSE marker or DONE mismatch"
            )
            return response.status_code, valid, error

        operations = {
            "live": live,
            "auth_me": auth_me,
            "chat": chat,
            "sse": sse,
        }
        results: list[ScenarioResult] = []
        scenario_names = (
            ("live", "auth_me", "chat", "sse")
            if args.scenario == "all"
            else (args.scenario,)
        )
        for name in scenario_names:
            requests, concurrency = profile[name]
            if args.requests is not None:
                requests = max(1, args.requests)
            if args.concurrency is not None:
                concurrency = max(1, args.concurrency)
            results.append(
                await _run_scenario(
                    name=name,
                    requests=requests,
                    concurrency=concurrency,
                    operation=operations[name],
                )
            )

    payload = {
        "profile": args.profile,
        "scenario": args.scenario,
        "base_url": args.base_url,
        "note": "Local controlled-stub measurement; not a production capacity claim.",
        "results": [asdict(result) for result in results],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 1 if any(result.failures for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
