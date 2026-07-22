from __future__ import annotations

import json
import os

from backend.env import load_env


def main() -> int:
    load_env()

    from pymilvus import MilvusClient

    host = os.getenv("MILVUS_HOST", "127.0.0.1")
    port = os.getenv("MILVUS_PORT", "19530")
    client = MilvusClient(uri=f"http://{host}:{port}")
    try:
        collections = []
        for name in sorted(client.list_collections()):
            description = client.describe_collection(name)
            collections.append(
                {
                    "name": name,
                    "row_count": int(client.get_collection_stats(name).get("row_count", 0)),
                    "fields": [field["name"] for field in description.get("fields", [])],
                }
            )
        print(json.dumps({"uri": f"http://{host}:{port}", "collections": collections}, indent=2))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
