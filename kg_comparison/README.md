# `kg_comparison`：知识图谱抽取基线对比实验框架

本目录用于开展 **`RAG_drift` 与主流开源知识图谱抽取范式（KGGen、GraphRAG 等）的受控对比实验**。所有方法统一对齐底层模型（OpenAI-compatible 接口、`temperature=0`）与输入切片边界（跳过各自自带分块器，直接传入相同的 `title + chunk.text`），并通过统一评测口径输出精度、结构质量、溯源能力与 Token 成本对比。

---

## 一、目录结构与设计原则

```text
kg_comparison/
├── __init__.py
├── README.md
├── schema.py                # 统一中间格式 kg-comparison-v1
├── adapter.py               # 格式适配器（绕过 QualityReview、保留大小写、默认值填充）
├── metrics.py               # 本体无关结构/溯源/Token/MINE-1 指标
├── evaluate_baseline.py     # Step 1：独立基线评测器与 --compare 多方法出表
├── llm_client.py            # 共享 OpenAI-compatible 客户端与磁盘缓存
├── kggen/                   # Step 2：KGGen 对比基线（独立目录）
│   ├── __init__.py
│   ├── __main__.py
│   ├── engine.py
│   └── run.py
└── graphrag/                # Step 3：GraphRAG 对比基线（独立目录）
    ├── __init__.py
    ├── __main__.py
    ├── engine.py
    └── run.py
```

### 关键对齐与公平性保障

1. **绕过 `QualityReview` 强校验**：外部基线不产出 `QualityReview` 凭据，且开放域抽取允许产出长句/开放谓词或标题泄漏实体；`evaluate_baseline.py` 不会整段判零，而是忠实计分并单列 `ungrounded_entities`。
2. **保留原文大小写与 `surface_text`**：在聚合、KGGen Stage 3 聚类或 GraphRAG 跨 `TextUnit` Merge 投影回单切片时，始终保留该切片中出现的原始大小写 `mentions`，防止破坏逐字溯源定位。
3. **默认限定与极性填充**：`KGGen-Open`、`GraphRAG-Open` 等不区分 `assertion` 与 `qualifiers` 的基线，适配层自动填充默认值（`assertion="asserted"`、`qualifiers={}`）并统计填充次数。
4. **GraphRAG 专项适配**：关闭 Leiden 社区发现与社区摘要（保留 `Extraction + Gleaning + Element Merge`），提供 `open`（原生态长句叙述边）、`open_mapped`（复用 `open` 缓存做后置本体映射）与 `constrained`（同本体注入抽取）三种对照模式，并全程保留 `text_unit_ids` 溯源。

---

## 二、快速使用指南（产物建议写入已忽略的 `data/kg_comparison_runs/`）

### 1. Dry-run 检查（不调用模型、不消耗 Token）

```bash
# KGGen dry-run
.venv/bin/python -m kg_comparison.kggen.run --mode open --granularity per_chunk --dry-run
.venv/bin/python -m kg_comparison.kggen.run --mode constrained --granularity corpus_batch --dry-run

# GraphRAG dry-run（均显示 community_detection_enabled: false）
.venv/bin/python -m kg_comparison.graphrag.run --mode open --granularity per_chunk --dry-run
.venv/bin/python -m kg_comparison.graphrag.run --mode open_mapped --granularity per_chunk --dry-run
.venv/bin/python -m kg_comparison.graphrag.run --mode constrained --granularity corpus_batch --dry-run
```

### 2. Step 2：运行 KGGen 对比基线（支持断点续跑与磁盘缓存）

```bash
# 2.1 KGGen-Open（per_chunk）
.venv/bin/python -m kg_comparison.kggen.run \
  --mode open --granularity per_chunk \
  --response-cache data/kg_comparison_runs/cache \
  --output data/kg_comparison_runs/kggen_open_per_chunk.preds.json \
  --eval-output data/kg_comparison_runs/kggen_open_per_chunk.eval.json \
  --eval-report data/kg_comparison_runs/kggen_open_per_chunk.md

# 2.2 KGGen-Open（corpus_batch，开启 Stage 2/3 聚类消歧）
.venv/bin/python -m kg_comparison.kggen.run \
  --mode open --granularity corpus_batch \
  --response-cache data/kg_comparison_runs/cache \
  --output data/kg_comparison_runs/kggen_open_corpus_batch.preds.json \
  --eval-output data/kg_comparison_runs/kggen_open_corpus_batch.eval.json \
  --eval-report data/kg_comparison_runs/kggen_open_corpus_batch.md

# 2.3 KGGen-Constrained（同本体约束，per_chunk）
.venv/bin/python -m kg_comparison.kggen.run \
  --mode constrained --granularity per_chunk \
  --response-cache data/kg_comparison_runs/cache \
  --output data/kg_comparison_runs/kggen_constrained_per_chunk.preds.json \
  --eval-output data/kg_comparison_runs/kggen_constrained_per_chunk.eval.json \
  --eval-report data/kg_comparison_runs/kggen_constrained_per_chunk.md

# 2.4 KGGen-Constrained + 认知极性预测（corpus_batch）
.venv/bin/python -m kg_comparison.kggen.run \
  --mode constrained --granularity corpus_batch --with-assertion \
  --response-cache data/kg_comparison_runs/cache \
  --output data/kg_comparison_runs/kggen_constrained_assert_batch.preds.json \
  --eval-output data/kg_comparison_runs/kggen_constrained_assert_batch.eval.json \
  --eval-report data/kg_comparison_runs/kggen_constrained_assert_batch.md
```

### 3. Step 3：运行 GraphRAG 对比基线（关闭社区发现，保留 Gleaning + Merge）

```bash
# 3.1 GraphRAG-Open（原生态联合抽取 + 1 轮 Gleaning，per_chunk）
.venv/bin/python -m kg_comparison.graphrag.run \
  --mode open --granularity per_chunk --max-gleanings 1 \
  --response-cache data/kg_comparison_runs/cache \
  --output data/kg_comparison_runs/graphrag_open_per_chunk.preds.json \
  --eval-output data/kg_comparison_runs/graphrag_open_per_chunk.eval.json \
  --eval-report data/kg_comparison_runs/graphrag_open_per_chunk.md

# 3.2 GraphRAG-Open（corpus_batch 跨 TextUnit Element Merge，记录 text_unit_ids）
.venv/bin/python -m kg_comparison.graphrag.run \
  --mode open --granularity corpus_batch --max-gleanings 1 \
  --response-cache data/kg_comparison_runs/cache \
  --output data/kg_comparison_runs/graphrag_open_corpus_batch.preds.json \
  --eval-output data/kg_comparison_runs/graphrag_open_corpus_batch.eval.json \
  --eval-report data/kg_comparison_runs/graphrag_open_corpus_batch.md

# 3.3 GraphRAG-Open-Mapped（自动复用 3.1 的缓存抽取结果，仅补跑后置本体映射）
.venv/bin/python -m kg_comparison.graphrag.run \
  --mode open_mapped --granularity per_chunk --max-gleanings 1 \
  --response-cache data/kg_comparison_runs/cache \
  --output data/kg_comparison_runs/graphrag_open_mapped_per_chunk.preds.json \
  --eval-output data/kg_comparison_runs/graphrag_open_mapped_per_chunk.eval.json \
  --eval-report data/kg_comparison_runs/graphrag_open_mapped_per_chunk.md

# 3.4 GraphRAG-Constrained（同本体注入 + Gleaning + 认知极性预测，per_chunk）
.venv/bin/python -m kg_comparison.graphrag.run \
  --mode constrained --granularity per_chunk --max-gleanings 1 --with-assertion \
  --response-cache data/kg_comparison_runs/cache \
  --output data/kg_comparison_runs/graphrag_constrained_assert_per_chunk.preds.json \
  --eval-output data/kg_comparison_runs/graphrag_constrained_assert_per_chunk.eval.json \
  --eval-report data/kg_comparison_runs/graphrag_constrained_assert_per_chunk.md
```

### 4. Step 1：导入 `RAG_drift` 原生结果与多基线横向汇总出表

```bash
# 4.1 导入 RAG_drift 原生评测输出
.venv/bin/python -m kg_comparison.evaluate_baseline \
  --predictions data/video_graph_runs/relation-gold-candidate-scope-20261119.json \
  --output data/kg_comparison_runs/rag_drift_native.eval.json \
  --report data/kg_comparison_runs/rag_drift_native.md

# 4.2 生成包含 RAG_drift + KGGen + GraphRAG 的横向对比汇总表
.venv/bin/python -m kg_comparison.evaluate_baseline \
  --compare \
    data/kg_comparison_runs/rag_drift_native.eval.json \
    data/kg_comparison_runs/kggen_constrained_per_chunk.eval.json \
    data/kg_comparison_runs/kggen_constrained_assert_batch.eval.json \
    data/kg_comparison_runs/kggen_open_per_chunk.eval.json \
    data/kg_comparison_runs/kggen_open_corpus_batch.eval.json \
    data/kg_comparison_runs/graphrag_constrained_assert_per_chunk.eval.json \
    data/kg_comparison_runs/graphrag_open_mapped_per_chunk.eval.json \
    data/kg_comparison_runs/graphrag_open_per_chunk.eval.json \
    data/kg_comparison_runs/graphrag_open_corpus_batch.eval.json \
  --output data/kg_comparison_runs/comparison_summary.json \
  --report data/kg_comparison_runs/comparison_summary.md
```

---

## 三、后续步骤规划

- **Step 4（统一成本-精度帕累托分析与 MINE-1 基准）**：基于 `metrics.evaluate_mine1_facts` 在开放原子事实集上完成跨范式事实保留度评测，并输出双赛道汇总报表。
