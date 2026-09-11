# Data governance and quality

## Required metadata

| Field | Purpose | Validation |
| --- | --- | --- |
| `doc_id` | Stable document identity | Unique and non-empty |
| `title` | Retrieval and citation title | Non-empty |
| `city` | Geographic filter | Standard city name or `通用` |
| `category` | Business taxonomy | Controlled vocabulary |
| `updated_at` | Freshness monitoring | ISO date |
| `sha256` | Incremental change detection | Generated during ingestion |
| `source` | Evidence traceability | File path or approved URL |

## Observable metrics

- Parse success rate
- Required-field completeness
- Duplicate document rate
- Chunk length distribution
- Incremental index update count
- Recall@K and MRR@K
- Intent routing accuracy
- Citation document accuracy
- P50 and P95 latency
- Positive feedback rate and unresolved-question rate

