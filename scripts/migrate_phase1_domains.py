import argparse
import json

from backend.env import load_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply or safely roll back HealthTrace Phase 1")
    parser.add_argument("action", choices=("apply", "rollback", "status"), nargs="?", default="apply")
    args = parser.parse_args()
    load_env()

    from backend.infra.database import engine
    from backend.infra.migrations import (
        apply_phase1_migration,
        get_phase1_migration_status,
        rollback_phase1_migration,
    )

    if args.action == "apply":
        result = apply_phase1_migration(engine)
    elif args.action == "rollback":
        result = rollback_phase1_migration(engine)
    else:
        result = get_phase1_migration_status(engine)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
