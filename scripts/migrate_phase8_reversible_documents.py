import argparse
import json

from backend.env import load_env


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage HealthTrace Phase 8 migration")
    parser.add_argument("action", choices=["status", "apply", "rollback"])
    args = parser.parse_args()
    load_env()

    from backend.infra.database import engine
    from backend.infra.migrations import (
        apply_phase8_reversible_document_migration,
        get_phase8_migration_status,
        rollback_phase8_reversible_document_migration,
    )

    if args.action == "apply":
        result = apply_phase8_reversible_document_migration(engine)
    elif args.action == "rollback":
        result = rollback_phase8_reversible_document_migration(engine)
    else:
        result = get_phase8_migration_status(engine)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
