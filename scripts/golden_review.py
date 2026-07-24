import argparse
import json
from pathlib import Path

from backend.env import PROJECT_ROOT, load_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage HealthTrace golden review workflow")
    sub = parser.add_subparsers(dest="action", required=True)
    import_cmd = sub.add_parser("import")
    import_cmd.add_argument("--path", default="evaluation/healthtrace_agent_v1.jsonl")
    import_cmd.add_argument("--dataset", default="healthtrace_agent")
    import_cmd.add_argument("--version", default="v1")
    status_cmd = sub.add_parser("status")
    status_cmd.add_argument("--dataset", default="healthtrace_agent")
    status_cmd.add_argument("--version", default="v1")
    export_cmd = sub.add_parser("export")
    export_cmd.add_argument("--dataset", default="healthtrace_agent")
    export_cmd.add_argument("--version", default="v1")
    export_cmd.add_argument(
        "--output",
        default="data/evaluations/golden/approved_healthtrace_agent_v1.jsonl",
    )
    args = parser.parse_args()
    load_env()

    from backend.evaluation.golden_review import (
        export_approved_cases,
        golden_readiness,
        import_jsonl_cases,
    )
    from backend.infra.database import SessionLocal

    db = SessionLocal()
    try:
        if args.action == "import":
            source = (PROJECT_ROOT / args.path).resolve()
            source.relative_to((PROJECT_ROOT / "evaluation").resolve())
            result = import_jsonl_cases(
                db,
                path=source,
                dataset_name=args.dataset,
                dataset_version=args.version,
                clinical_review_required=True,
            )
            db.commit()
            result["readiness"] = golden_readiness(db, args.dataset, args.version)
        elif args.action == "status":
            result = golden_readiness(db, args.dataset, args.version)
        else:
            target = (PROJECT_ROOT / args.output).resolve()
            target.relative_to((PROJECT_ROOT / "data" / "evaluations").resolve())
            count = export_approved_cases(
                db,
                dataset_name=args.dataset,
                dataset_version=args.version,
                output_path=target,
            )
            result = {"exported": count, "output": str(target)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

