import argparse
import json

from backend.env import load_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage HealthTrace Phase 2 fact candidates")
    parser.add_argument("action", choices=("apply", "rollback", "status"), nargs="?", default="apply")
    args = parser.parse_args()
    load_env()

    from backend.infra.database import engine
    from backend.infra.migrations import (
        apply_phase2_fact_candidate_migration,
        get_phase2_migration_status,
        rollback_phase2_fact_candidate_migration,
    )

    if args.action == "apply":
        result = apply_phase2_fact_candidate_migration(engine)
    elif args.action == "rollback":
        result = rollback_phase2_fact_candidate_migration(engine)
    else:
        result = get_phase2_migration_status(engine)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
