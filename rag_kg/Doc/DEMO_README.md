# 中节能 zjn RAG+KG 骨架 v1.0 — 给潘云泓

> 撰稿: **徐简** (Xu Jian)
> 周期: 2026-05-01 ~ 2026-06-08
> 模块: 知识图谱 + 智能问答

## 1. 这是什么

中节能 (zjn) 环保执法领域的 **RAG + 知识图谱** 骨架 MVP。

- **图谱**: 2209 节点 / 8059 关系 (NetworkX 内存, 6 月可切 Neo4j)
- **检索**: 图 + 向量 + BM25 三路融合 (RRF 加权), 100 题评估 recall 58%
- **模型**: 智谱 GLM-4.7-Flash (30B, 合同范围)
- **智能体**: 5 个 (qa / nl2sql / tracing / compliance / fraud) 全跑通

## 2. 5 分钟上手 (3 步)

### Step 0: 准备环境

```powershell
# 用 conda 切到 zjn env (Python 3.11)
conda activate zjn

# 安装依赖
pip install zai-sdk sentence-transformers rank_bm25 chromadb networkx jieba pyyaml tiktoken pyvis neo4j
```

### Step 1: 重建图谱 (~3-5 分钟)

把甲方数据 `DataReal/` 挂到项目根 (项目组共享路径), 然后:

```powershell
cd E:\Gs_projects\THU_Projects\ChinaEnergyConservation
python setup.py
```

预期输出:
```
================================================================
  重建完成 ✓
  图谱: 2209 节点 / 8059 关系
  节点类型: {'Article': 114, 'Pollutant': 129, ...}
  耗时: 200s
================================================================
```

### Step 2: 跑演示 (~5 分钟, 7 个真实业务问题)

```powershell
python kg\demo_for_panyh.py
```

会问 7 个问题, 每个都有证据展示:
- 3 个 **qa** (智能问答)
- 1 个 **tracing** (大气污染溯源)
- 1 个 **compliance** (企业合规)
- 1 个 **fraud** (报告造假识别)
- 1 个 **nl2sql** (智能问数)

每个问题可按回车继续。

## 3. 文件结构

```
ChinaEnergyConservation/
├── setup.py                  ← 一键重建图谱
├── DataReal/                 ← (不在本包) 甲方数据 3384 份 metadata.json
├── Doc/
│   └── MONTHLY_REPORT_2026-05.md   ← 5 月月报
└── kg/                       (~880KB)
    ├── skeleton_v1.py        ← 端到端 demo
    ├── demo_for_panyh.py     ← ★ 演示入口
    ├── ontology/             ← 本体 yaml (16 节点 + 22 关系)
    ├── extract/              ← chunker + LLM 抽取器
    ├── import_db/            ← 图谱构建 (NetworkX + Neo4j 接口)
    ├── embed/                ← 嵌入 (智谱 API / 本地 BGE)
    ├── retrieve/             ← 混合检索 (RRF)
    ├── rag/                  ← RAG 链 + text2Cypher
    ├── skills/               ← 5 智能体
    ├── eval/                 ← 100 题评估集 + 评估脚本
    ├── visualize/            ← pyvis 图谱可视化
    ├── logs/
    │   └── extractions_200.jsonl  ← ★ 关键数据 530KB (LLM 抽取结果)
    └── tests/                ← 测试脚本
```

## 4. 关键数据

| 资产 | 数量 | 位置 |
|---|---:|---|
| 知识图谱节点 | 2209 | `kg/import_db/graph_store/graph_full.json` (2.7MB) |
| 知识图谱关系 | 8059 | 同上 |
| LLM 抽取结果 | 117 篇 / 530KB | `kg/logs/extractions_200.jsonl` |
| 评估集 | 100 题 | `kg/eval/dataset_v1.json` |
| 评估 baseline | recall 58% | `kg/logs/recall_v1_230905.json` |
| 本体定义 | 16 节点 + 22 关系 | `kg/ontology/environmental_kg.yaml` |

## 5. 评估结果 (100 题, recall-only 模式)

| 类型 | 通过率 | 题数 | 状态 |
|---|---:|---:|:---:|
| standard_limit | **100%** | 21 | ✅ |
| industry_compliance | **71%** | 21 | ✅ |
| law_article | **65%** | 23 | 🟡 |
| case_recommend | **20%** | 20 | 🟡 |
| process_sop | **20%** | 15 | 🟡 |
| **总** | **58%** | 100 | 🟡 (目标 85%) |

## 6. 6 月计划 (下一步)

| 优先级 | 任务 | 估时 |
|:---:|---|---:|
| 🔴 P0 | 全量 LLM 抽取 7100 Article (后台, 8-12h) | 1d |
| 🔴 P0 | ChromaDB 全量 36980 嵌入 (智谱 API, 限流中) | 0.5d |
| 🔴 P0 | RRF 权重深度调参 + 评估集 100 题完整 baseline | 1.5d |
| 🟡 P1 | Neo4j 切换 (替换 NetworkX) | 0.5d |
| 🟡 P1 | 5 智能体接入 Dify 编排 | 3d |
| 🟡 P1 | FastAPI endpoint v1 (给业务侧调用) | 1d |

## 7. 常见问题

**Q: setup.py 找不到 DataReal?**
A: 编辑 `setup.py` 顶部 `DATA_ROOT` 变量, 改成实际路径

**Q: 评估里 case_recommend 只有 20% 召回?**
A: 抽取的 Case 节点数 (43) 太少, 需要更多 LLM 抽取; 全量 7100 篇跑完应该能补上

**Q: 演示里某些问题答得不全?**
A: 抽取层只覆盖了 117 篇 Article (1.6%), 全量 7100 篇跑完会有质的提升

**Q: Neo4j 切换?**
A: 抽象层已就绪 (`kg/import_db/graph_store.py:GraphStoreProtocol`), 装 JDK + Neo4j 后改环境变量 `GRAPH_BACKEND=neo4j` 即可

## 8. 联系

徐简 (Xu Jian)
邮箱/飞书: (项目内)
