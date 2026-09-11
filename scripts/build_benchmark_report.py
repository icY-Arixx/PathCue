from __future__ import annotations

import json
from pathlib import Path


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    evaluation = json.loads((root / "evaluation_report.json").read_text(encoding="utf-8"))
    quality = json.loads((root / "data_quality_report.json").read_text(encoding="utf-8"))
    metrics = evaluation["metrics"]
    report = f"""# 城市出行与空间知识 RAG 基线报告

## 数据集

- 知识文档：{quality['document_count']} 篇，包含 Markdown、DOCX 与 PDF
- 文档分块：{quality['chunk_count']} 个
- 评测问题：{evaluation['dataset_size']} 条，其中可回答 {evaluation['positive_count']} 条、困难负样本 {evaluation['hard_negative_count']} 条
- Embedding：{evaluation['configuration']['embedding_model']}
- 向量数据库：{evaluation['configuration']['vector_backend']}（Milvus 实测证据见 `artifacts/milvus_verification.json`）

## 数据质量

- 元数据完整率：{quality['metadata_completeness']:.2%}
- 文档内容重复率：{quality['duplicate_document_rate']:.2%}
- 重复文档 ID：{quality['duplicate_doc_id_count']}
- 重复分块：{quality['duplicate_chunk_content_count']}

## 检索与回答

- Recall@1：{metrics['recall_at_1']:.2%}
- Recall@3：{metrics['recall_at_3']:.2%}
- Recall@5：{metrics['recall_at_5']:.2%}
- Recall@8：{metrics['recall_at_8']:.2%}
- MRR@8：{metrics['mrr_at_8']:.4f}
- 引用文档准确率：{metrics['citation_doc_accuracy']:.2%}
- 困难负样本拒答率：{metrics['hard_negative_rejection_rate']:.2%}
- P50 延迟：{metrics['latency_p50_ms']:.2f} ms
- P95 延迟：{metrics['latency_p95_ms']:.2f} ms

## 结论

该结果是小规模演示语料上的可复现基线。Recall@8 已覆盖全部正样本，但引用选择和困难负样本拒答仍有提升空间，后续应通过更强重排序模型、领域负样本扩充和证据阈值校准继续迭代。
"""
    output = root / "docs" / "benchmark_report.md"
    output.write_text(report, encoding="utf-8")
    print(output)
