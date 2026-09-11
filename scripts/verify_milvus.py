from __future__ import annotations

import json
from dataclasses import asdict

from urban_mobility_rag.app_factory import build_runtime
from urban_mobility_rag.config import Settings


if __name__ == "__main__":
    settings = Settings.from_env()
    if settings.vector_backend != "milvus":
        raise SystemExit("Set VECTOR_BACKEND=milvus and MILVUS_URI before running this verification.")
    pipeline, _, first = build_runtime(settings, run_index=True)
    _, _, second = build_runtime(settings, run_index=True)
    dense_store = pipeline.retriever.vector_store

    def entity_count() -> int:
        rows = dense_store.client.query(
            settings.milvus_collection,
            filter="",
            output_fields=["count(*)"],
        )
        return int(rows[0]["count(*)"])

    baseline_count = entity_count()
    probe_path = settings.knowledge_dir / "incremental_update_probe.md"
    try:
        probe_path.write_text(
            "# 增量更新验证\n\n临时接驳点位于测试路口东侧，服务时间为每日 07:00—09:00。",
            encoding="utf-8",
        )
        _, _, incremental_create = build_runtime(settings, run_index=True)
        count_after_create = entity_count()
        probe_path.write_text(
            "# 增量更新验证\n\n临时接驳点位于测试路口西侧，服务时间调整为每日 17:00—19:00。",
            encoding="utf-8",
        )
        _, _, incremental_update = build_runtime(settings, run_index=True)
        count_after_update = entity_count()
    finally:
        probe_path.unlink(missing_ok=True)
        _, _, incremental_cleanup = build_runtime(settings, run_index=True)
    count_after_cleanup = entity_count()
    result = pipeline.query("道路施工公告需要记录哪些字段？")
    payload = {
        "uri": settings.milvus_uri,
        "collection": settings.milvus_collection,
        "entity_count": count_after_cleanup,
        "first_index_run": asdict(first) if first else None,
        "second_index_run": asdict(second) if second else None,
        "incremental_update": {
            "create": asdict(incremental_create),
            "update": asdict(incremental_update),
            "cleanup": asdict(incremental_cleanup),
            "counts": {
                "baseline": baseline_count,
                "after_create": count_after_create,
                "after_update": count_after_update,
                "after_cleanup": count_after_cleanup,
            },
        },
        "retrieval": {"answer": result.answer, "citations": [item.doc_id for item in result.citations]},
    }
    artifact_path = settings.project_root / "artifacts" / "milvus_verification.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
