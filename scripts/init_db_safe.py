from backend.env import load_env


def main() -> int:
    load_env()
    try:
        from backend.infra.database import init_db

        init_db()
        print("[OK] Database initialized")
    except Exception as exc:
        print(f"[WARN] Database initialization skipped: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
