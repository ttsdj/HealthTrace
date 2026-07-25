import argparse
import json

from backend.env import load_env


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply or safely disable HealthTrace Phase 7 incremental updates"
    )
    parser.add_argument("action", choices=["status", "apply", "rollback"])
    args = parser.parse_args()
    load_env()

    from backend.infra.database import engine
    from backend.infra.migrations import (
        apply_phase7_incremental_document_migration,
        get_phase7_migration_status,
        rollback_phase7_incremental_document_migration,
    )

    handlers = {
        "status": get_phase7_migration_status,
        "apply": apply_phase7_incremental_document_migration,
        "rollback": rollback_phase7_incremental_document_migration,
    }
    print(json.dumps(handlers[args.action](engine), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
