from dataclasses import asdict
import json

from urban_mobility_rag.app_factory import build_runtime
from urban_mobility_rag.config import Settings


if __name__ == "__main__":
    _, _, report = build_runtime(Settings.from_env(), run_index=True)
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))

