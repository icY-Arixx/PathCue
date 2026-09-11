import json
import sys

from urban_mobility_rag.app_factory import build_runtime
from urban_mobility_rag.config import Settings


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "夜间骑行需要注意什么？"
    pipeline, _, _ = build_runtime(Settings.from_env())
    print(json.dumps(pipeline.query(question).to_dict(), ensure_ascii=False, indent=2))

