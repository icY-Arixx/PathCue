import json
from pathlib import Path

from urban_mobility_rag.chunking import MetadataAwareChunker
from urban_mobility_rag.config import Settings
from urban_mobility_rag.quality import analyze_corpus


if __name__ == "__main__":
    settings = Settings.from_env()
    report = analyze_corpus(settings.knowledge_dir, MetadataAwareChunker(settings.chunk_size, settings.chunk_overlap))
    output = Path(__file__).resolve().parents[1] / "data_quality_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

