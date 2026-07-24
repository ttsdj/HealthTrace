import argparse
import json

from backend.env import load_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply or safely disable HealthTrace Phase 5")
    parser.add_argument("action", choices=["status", "apply", "rollback"])
    args = parser.parse_args()
    load_env()

    from backend.infra.database import engine
    from backend.infra.migrations import (
        apply_phase5_access_security_migration,
        get_phase5_migration_status,
        rollback_phase5_access_security_migration,
    )

    handlers = {
        "status": get_phase5_migration_status,
        "apply": apply_phase5_access_security_migration,
        "rollback": rollback_phase5_access_security_migration,
    }
    print(json.dumps(handlers[args.action](engine), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
