from __future__ import annotations

import argparse
import json

from backend.env import load_env
from backend.indexing.milvus_client import COLLECTION_ENV_BY_KIND, MilvusStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create missing HealthTrace Milvus collections without changing existing data"
    )
    parser.add_argument(
        "--kinds",
        default="patient_record",
        help=f"Comma-separated kinds: {','.join(COLLECTION_ENV_BY_KIND)}",
    )
    args = parser.parse_args()
    load_env()

    requested = [item.strip() for item in args.kinds.split(",") if item.strip()]
    unsupported = sorted(set(requested) - set(COLLECTION_ENV_BY_KIND))
    if unsupported:
        parser.error(f"unsupported collection kinds: {', '.join(unsupported)}")

    initialized = []
    for kind in requested:
        store = MilvusStore.for_kind(kind)
        store.init_collection()
        initialized.append({"kind": kind, "collection_name": store.collection_name})

    print(json.dumps({"status": "ready", "collections": initialized}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
