import argparse
import json

from backend.env import load_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage HealthTrace Phase 3 long-term tasks")
    parser.add_argument("action", choices=("apply", "rollback", "status"), nargs="?", default="apply")
    args = parser.parse_args()
    load_env()

    from backend.infra.database import engine
    from backend.infra.migrations import (
        apply_phase3_long_term_task_migration,
        get_phase3_migration_status,
        rollback_phase3_long_term_task_migration,
    )

    if args.action == "apply":
        result = apply_phase3_long_term_task_migration(engine)
    elif args.action == "rollback":
        result = rollback_phase3_long_term_task_migration(engine)
    else:
        result = get_phase3_migration_status(engine)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
