import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def _read_json(url: str, timeout: float) -> tuple[int, dict]:
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, json.loads(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a deployed HealthTrace API")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--wait-seconds", type=int, default=120)
    args = parser.parse_args()
    deadline = time.monotonic() + max(1, args.wait_seconds)
    last_error = ""
    while time.monotonic() < deadline:
        try:
            live_status, live = _read_json(f"{args.base_url}/health/live", 5)
            ready_status, ready = _read_json(f"{args.base_url}/health/ready", 10)
            if (
                live_status == 200
                and live.get("service") == "HealthTrace"
                and ready_status == 200
                and ready.get("ready") is True
            ):
                print(
                    json.dumps(
                        {
                            "status": "passed",
                            "service": ready["service"],
                            "infra_mode": ready["infra_mode"],
                            "core_services": ready["core_services"],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            last_error = f"live={live_status}, ready={ready_status}"
        except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2)
    print(json.dumps({"status": "failed", "error": last_error}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

