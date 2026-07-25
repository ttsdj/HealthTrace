import argparse
import json

from backend.env import load_env


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Purge expired HealthTrace cold vectors without deleting raw files"
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    load_env()

    from backend.indexing.version_retention import purge_expired_document_versions
    from backend.infra.database import SessionLocal

    db = SessionLocal()
    try:
        result = purge_expired_document_versions(
            db,
            limit=args.limit,
            dry_run=not args.execute,
        )
        if args.execute:
            db.commit()
        else:
            db.rollback()
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
