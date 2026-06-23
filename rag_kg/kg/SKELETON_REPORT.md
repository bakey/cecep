# 环保执法 RAG + KG 骨架 v1 (交付报告)

> 日期: 2026-06-01
> 状态: 骨架全栈跑通
> 后续: 补 LLM 抽取 + 部署 Neo4j

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│  Agent / Dify 编排                                          │
└─────────────────┬───────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────────────────────────┐
│  5 Skills (kg/skills/agent_skills.py + skeleton_v1.py)     │
│    ├─ qa         (专业问答)                                  │
│    ├─ nl2sql     (智能问数)                                  │
│    ├─ tracing    (大气污染溯源)                              │
│    ├─ compliance (企业合规)                                  │
│    └─ fraud      (报告造假)                                  │
└─────────────────┬───────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────────────────────────┐
│  RAG Chain (kg/rag/qa_chain.py)                             │
│    └─ GLM-4.7-Flash 回答生成                                 │
└─────────────────┬───────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────────────────────────┐
│  Hybrid Retriever (RRF 融合)                                 │
│    ├─ Vector: ChromaDB (7000 向量) + BGE-small-zh           │
│    ├─ Graph:  NetworkXGraphStore (1172 节点 / 7087 关系)    │
│    └─ BM25:   jieba + rank_bm25 (2000 文档)                  │
└─────────────────┬───────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────────────────────────┐
│  数据层 (DataReal/)                                         │
│    ├─ 3384 个 metadata.json (结构化)                        │
│    ├─ 36980 个 chunks (Article 7100 + Window 29878)         │
│    └─ 20 个 LLM 抽取样本 (extractions_20.jsonl)              │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 已交付模块

| 模块 | 文件 | 状态 |
|---|---|---|
| 本体定义 | `kg/ontology/environmental_kg.yaml` | ✅ 16 节点 + 22 关系 |
| 分块器 | `kg/extract/chunker.py` | ✅ 36980 chunks |
| LLM 抽取 | `kg/extract/llm_extractor.py` | ✅ 20/20 成功 (新 key) |
| 图存储 | `kg/import_db/graph_store.py` | ✅ NetworkX + Neo4j 抽象 |
| 嵌入 | `kg/embed/vector_embedder.py` | ✅ BGE 512 维 + ChromaDB |
| 混合检索 | `kg/skeleton_v1.py` HybridRetriever | ✅ RRF 融合 |
| RAG | `kg/skeleton_v1.py` RAGChain | ✅ GLM-4.7-Flash |
| 5 Skills | `kg/skeleton_v1.py` SKILL_REGISTRY | ✅ |
| 集成 Demo | `kg/skeleton_v1.py` main() | ✅ 7/8 通过 |
| 抽取导入 | `kg/import_db/import_extractions.py` | ✅ |
| 依赖管理 | `zjn` conda env | ✅ |

---

## 3. 数据统计

### 3.1 图谱 (kg/import_db/graph_store/graph_full.json)

```
节点: 1172
  Law: 49
  Regulation: 27
  Standard: 29
  Document: 606
  Article: 20         ← LLM 抽取
  Pollutant: 60       ← LLM 抽取
  PollutionSource: 40 ← LLM 抽取
  Industry: 24        ← LLM 抽取
  TreatmentTech: 17   ← LLM 抽取
  Violation: 88       ← LLM 抽取
  Penalty: 40         ← LLM 抽取
  Case: 11            ← LLM 抽取
  Organization: 121
  Region: 40

关系: 7087
  ISSUED_BY: 2962
  APPLIES_TO_REGION: 2748
  SUPERSEDES: 1104
  ARTICLE_DEFINES_VIOLATION: 97  ← LLM
  ARTICLE_DEFINES_PENALTY: 65    ← LLM
  SOURCE_EMITS_POLLUTANT: 30     ← LLM
  TECH_TREATS_POLLUTANT: 19      ← LLM
  INDUSTRY_USES_TECH: 12         ← LLM
  CASE_*: 23                     ← LLM
  CONTAINS_ARTICLE: 20           ← LLM
  ORGANIZATION_*: 3              ← LLM
```

### 3.2 向量 (ChromaDB)

- 总数: 7000 chunks
- 模型: BAAI/bge-small-zh-v1.5 (512 维)
- 缓存: disk jsonl

### 3.3 BM25

- 文档数: 2000 (chunks)
- 分词: jieba.cut_for_search

---

## 4. 测试结果

| # | Skill | 查询 | 结果 |
|---|---|---|---|
| 1 | nl2sql | 查询河北省的所有地方标准 | ✅ 命中 4 个节点 |
| 2 | tracing | SO2 的主要污染源 | ✅ 找到 SO2/烟尘/废水 |
| 3 | qa | 水污染防治法的处罚条款 | ✅ 引用第九十一条 |
| 4 | fraud | 监测数据造假有什么处罚 | ✅ 刑法 287 + 罚款条款 |
| 5 | compliance | 河北省 地方 标准 | ⚠️ graph 无"地方标准"分类节点 |
| 6 | qa | 危险废物相关的处罚规定 | ✅ 引用第 112 条 + 5 类违法 |
| 7 | tracing | 工业废水的主要污染物 | ✅ 找到废水/水污染物 |
| 8 | qa | 大气污染排放标准 | ✅ HJ 76-2017 / HJ 75-2017 |

**通过率: 7/8 (87.5%)**

---

## 5. 已知问题 & 后续

### 5.1 立即修复 (1 周)
- [ ] **LLM 抽取全量**: 7100 Article chunks, 需 8-12 小时
- [ ] **限流策略**: GLM API 限流 1305, 加 monitor + 动态 backoff
- [ ] **graph_full.json 持久化**: 现加载 ~3s, 后续加 LRU 缓存

### 5.2 短期 (2-3 周)
- [ ] **Neo4j 部署**: 装 Docker → 跑 `neo4j:5-community` → 切 `GRAPH_BACKEND=neo4j`
- [ ] **Phase 1 迁移**: 把 `KnowledgeGraph/phase1_graph.json` (3321 节点) 合并进来
- [ ] **text_search 增强**: 同义词、行业别名（如"废气" ↔ "工业废气"）
- [ ] **RRF 调参**: 当前 k=60, 验证 k=20/40/80 哪个好
- [ ] **评估集**: 准备 50-100 个标准问答对，量化指标

### 5.3 中期 (1-2 月)
- [ ] **多跳检索**: Article → Pollutant → Standard → Case
- [ ] **text2Cypher**: 用 GLM 拆解 → 生成 Cypher → 执行
- [ ] **Agent 编排**: 5 Skills 串联, 复杂问 = qa + tracing + compliance
- [ ] **Dify 集成**: 暴露 FastAPI endpoint

---

## 6. 依赖 (zjn conda env)

```
zai-sdk            0.2.0
sentence-transformers 5.2.2
rank_bm25          -
neo4j              6.2.0
chromadb           1.5.9
networkx           3.4.2
jieba              -
pyyaml             6.0.3
tiktoken           0.12.0
```

API Key: `f275cb076eab46d697c1285755ab4459.U1t2diOzRAwBYEWm` (智谱 GLM-4.7-Flash)

---

## 7. 关键文件

```
kg/
├── skeleton_v1.py                    ★ 主 demo (跑这个)
├── ontology/environmental_kg.yaml    本体
├── extract/
│   ├── chunker.py                    分块器
│   └── llm_extractor.py              LLM 抽取
├── embed/vector_embedder.py          BGE + ChromaDB
├── import_db/
│   ├── graph_store.py                ★ NetworkX + Neo4j 抽象
│   ├── import_extractions.py         LLM 抽取导入
│   ├── neo4j_importer.py             干跑 Cypher 生成
│   └── graph_store/graph_full.json   当前图谱
├── retrieve/hybrid_retriever.py      原混合检索 (待适配新 GraphStore)
├── rag/qa_chain.py                   原 RAG (待适配)
├── skills/agent_skills.py            原 5 Skills (待适配)
└── logs/                             chunks/extractions/graph 缓存
```

---

## 8. 启动命令

```bash
conda activate zjn
cd E:\Gs_projects\THU_Projects\ChinaEnergyConservation

# 完整骨架 demo
python kg/skeleton_v1.py

# 单独跑各模块
python kg/extract/chunker.py              # 分块
python kg/extract/llm_extractor.py 50     # LLM 抽取 50 条
python kg/import_db/import_extractions.py # 导入抽取到图
python kg/import_db/graph_store.py        # 从 metadata 重构图
python kg/embed/vector_embedder.py 10000  # 嵌入 10000 chunks
```

---

**下一步建议**: 你说"做完这个之后", 我可以:
1. 跑 7100 个 Article chunks 的 LLM 抽取 (后台跑 8-12h)
2. 装 Docker 起 Neo4j, 切到生产图存储
3. 准备评估集 (50-100 个标准问答), 量化指标
4. 写 FastAPI endpoint, 给 Dify 调用
