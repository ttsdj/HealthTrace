import argparse
import base64
import json
import os
from pathlib import Path

from backend.env import PROJECT_ROOT, load_env
from backend.infra.auth import MIN_JWT_SECRET_LENGTH, is_placeholder_secret


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate HealthTrace production configuration")
    parser.add_argument("--allow-optional-missing", action="store_true")
    args = parser.parse_args()
    load_env()
    checks: list[dict] = []

    def check(name: str, ok: bool, message: str, required: bool = True) -> None:
        checks.append(
            {
                "name": name,
                "ok": bool(ok),
                "required": required,
                "message": message,
            }
        )

    jwt_secret = os.getenv("JWT_SECRET_KEY", "")
    check(
        "jwt_secret",
        len(jwt_secret) >= MIN_JWT_SECRET_LENGTH and not is_placeholder_secret(jwt_secret),
        f"JWT_SECRET_KEY must be a non-placeholder secret of at least {MIN_JWT_SECRET_LENGTH} characters.",
    )
    raw_key = os.getenv("HEALTHTRACE_FIELD_ENCRYPTION_KEY", "")
    try:
        decoded_key = base64.urlsafe_b64decode(raw_key.encode("ascii"))
    except Exception:
        decoded_key = b""
    check(
        "field_encryption_key",
        len(decoded_key) == 32,
        "HEALTHTRACE_FIELD_ENCRYPTION_KEY must decode to 32 bytes.",
    )
    check(
        "llm_configuration",
        bool(
            os.getenv("BASE_URL")
            and os.getenv("MODEL")
            and (os.getenv("LLM_API_KEY") or os.getenv("ARK_API_KEY"))
        ),
        "BASE_URL, MODEL, and an LLM API key are required.",
    )
    origins = [
        item.strip()
        for item in os.getenv("CORS_ORIGINS", "").split(",")
        if item.strip()
    ]
    check(
        "cors_origins",
        bool(origins) and "*" not in origins,
        "CORS_ORIGINS must be explicit and must not contain '*'.",
    )
    check(
        "env_not_tracked",
        (PROJECT_ROOT / ".gitignore").is_file()
        and ".env" in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8"),
        ".env must remain ignored by Git.",
    )
    if os.getenv("HEALTHTRACE_EXTERNAL_NOTIFICATIONS_ENABLED", "false").lower() == "true":
        webhook = os.getenv("HEALTHTRACE_NOTIFICATION_WEBHOOK_URL", "")
        smtp = os.getenv("HEALTHTRACE_SMTP_HOST", "")
        check(
            "external_notification_provider",
            webhook.startswith("https://") or bool(smtp),
            "External notifications require a secure webhook or SMTP provider.",
            required=not args.allow_optional_missing,
        )
    failed = [item for item in checks if item["required"] and not item["ok"]]
    print(
        json.dumps(
            {
                "status": "passed" if not failed else "failed",
                "checks": checks,
                "failed_required_checks": [item["name"] for item in failed],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

