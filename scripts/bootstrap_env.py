import base64
import secrets
from pathlib import Path

from backend.env import PROJECT_ROOT


def _replace(
    lines: list[str],
    name: str,
    value: str,
    *,
    min_length: int = 0,
) -> list[str]:
    prefix = f"{name}="
    replaced = False
    output = []
    for line in lines:
        if line.startswith(prefix):
            current = line[len(prefix) :].strip()
            if len(current) < min_length or not current or any(
                marker in current.lower()
                for marker in ("replace", "change", "placeholder")
            ):
                output.append(prefix + value)
            else:
                output.append(line)
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(prefix + value)
    return output


def main() -> int:
    example = PROJECT_ROOT / ".env.example"
    target = PROJECT_ROOT / ".env"
    if target.exists():
        lines = target.read_text(encoding="utf-8-sig").splitlines()
        action = "updated safe local placeholders"
    else:
        lines = example.read_text(encoding="utf-8-sig").splitlines()
        action = "created from .env.example"
    example_lines = example.read_text(encoding="utf-8-sig").splitlines()
    existing_names = {
        line.split("=", 1)[0].strip()
        for line in lines
        if "=" in line and not line.lstrip().startswith("#")
    }
    missing_defaults = [
        line
        for line in example_lines
        if "=" in line
        and not line.lstrip().startswith("#")
        and line.split("=", 1)[0].strip() not in existing_names
    ]
    if missing_defaults:
        lines.extend(["", "# Added by HealthTrace configuration migration", *missing_defaults])
    lines = _replace(
        lines,
        "JWT_SECRET_KEY",
        secrets.token_urlsafe(48),
        min_length=32,
    )
    lines = _replace(
        lines,
        "HEALTHTRACE_FIELD_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f".env {action}.")
    print("Generated JWT and field-encryption secrets locally.")
    print("Still required: LLM_API_KEY, BASE_URL, MODEL, FAST_MODEL, GRADE_MODEL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
