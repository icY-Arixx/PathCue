# Resume material after implementation verification

## AI data development version

- Built an urban mobility knowledge pipeline covering multi-format parsing, metadata-aware chunking, checksum-based incremental indexing and traceable source management.
- Implemented BM25 and dense-vector hybrid retrieval, transparent reranking and citation-grounded answers; established Recall@8, MRR@8, routing accuracy, citation accuracy and latency metrics.
- Used LangGraph to route knowledge, POI and navigation scenarios, and exposed geocoding, POI search and route planning through an Amap MCP server and FastAPI.

## Current measured baseline

- Processed 7 mixed-format documents into 9 chunks with 100% required-metadata completeness and 0% duplicate-document rate.
- Evaluated 68 questions, including 12 hard negatives: Recall@1 78.57%, Recall@3 96.43%, Recall@8 100%, MRR@8 0.8762, citation-document accuracy 80.36%, and P95 local retrieval latency 1.95 ms.
- Verified a real 256-dimensional multilingual Model2Vec embedding: relevant cosine similarity 0.766712 versus 0.064465 for an unrelated sentence.

Do not describe Milvus or Amap as completed in the resume until `scripts/verify_milvus.py` and `scripts/amap_demo.py` have produced real outputs.

## Product and operations version

- Defined commuter and GIS-operator personas, decomposed knowledge Q&A, POI and navigation scenarios, and designed a query-feedback-evaluation iteration loop.
- Built an observable evaluation system around retrieval coverage, routing accuracy, citation quality, latency and negative-feedback categorization.

Only include measured values after running `python scripts/evaluate.py` in the target environment.
