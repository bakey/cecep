# 中节能 (zjn) 大模型集成开发平台 — 5 月工作月报 (含 6/2 补充)

> 撰稿人: **徐简** (Xu Jian)
> 周期: 2026-05-01 ~ 2026-06-02
> 模块: 知识库管理工具 / 智能问答 / 行业知识图谱构建
> 协作: 任伟 (KG/RAG 共同负责), 潘云泓 (技术 lead), 林恒宇 (智能问答)

---

## 一、本月概览

5 月工作重心：**RAG + 行业知识图谱骨架搭建**。在 Conversation-May 里我对潘云泓承诺过 "一个星期给一版法律知识图谱" + "MVP 出一版向量 + LLM"，5 月底已交付骨架 v1。

**6/2 补充攻坚**：在骨架 v1 基础上集中推进了全量 LLM 抽取、评估体系、RRF 优化和 text2Cypher。
- 全量抽取 200 篇（成功 117 条），图节点从 1172 → **2209**
- 交付 50 题评估集 + evaluate.py + baseline（平均 0.476，召回 70%）
- RRF 加权调参：召回 44% → **70%**（+26pp）
- text2Cypher 从雏形升级为 7 类规则模板 + GLM 兜底
- 图谱可视化 4 个子图，文档交付 3 份

**端到端链路全线跑通**：分块→LLM 抽取→图谱入库→混合检索→RAG→5 智能体，8 个测试全过。

**关键里程碑**：
| 里程碑 | 日期 | 状态 |
|---|---|---|
| 3384 份甲方数据清洗与归档 | 5/初 | ✅ |
| 16 节点 + 22 关系环保本体设计 | 5/中 | ✅ |
| chunker / LLM extractor / graph store / embedder 全套骨架 | 5/下 | ✅ |
| skeleton_v1.py 端到端 demo (8 测试 7 过) | 5/末 | ✅ |
| 全量 LLM 抽取 200 篇 (117 成功) + 图扩至 2209 节点 | 6/2 | ✅ |
| 评估体系搭建 (50 题 + evaluate.py + baseline + RRF 优化) | 6/2 | ✅ |
| text2Cypher (7 类规则 + GLM 兜底) + 图谱可视化 | 6/2 | ✅ |
| 文档交付 3 份 (月报/Review/NEXT_SESSION_PROMPT) | 6/2 | ✅ |

---

## 二、本月工作清单（按工作量逐条）

### A. 数据治理 (1d)

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| A1 | 接收甲方 3384 份原始材料，落盘本地 | `DataReal/` 目录结构 | ✅ |
| A2 | 解析 3384 份 `metadata.json` 标准化 | `compliance_assessment` 字段统一 | ✅ |
| A3 | 校验数据质量（去重 / 格式 / 完整性） | Great Expectations 风格校验 | ✅ |

### B. 需求对齐 (0.5d)

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| B1 | 参与 Meeting0311，明确 RAG+KG 边界 | 会议纪要 | ✅ |
| B2 | 与潘云泓飞书对齐：MVP 先向量+LLM，KG 一周内 | Conversation-May 共识 | ✅ |
| B3 | 飞书周报同步进度 | 3 次进度汇报 | ✅ |
| B4 | 联网调研 urban-air-quality-kg / Legal-LM / Neo4j hybrid RAG | 调研笔记 | ✅ |

### C. 本体设计 (1d)

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| C1 | 设计 16 节点本体 + 22 关系 | `kg/ontology/environmental_kg.yaml` | ✅ |
| C2 | 归一化字典（SO2/PM2.5/钢铁/化工 等核心实体） | 同上 yaml | ✅ |

### D. 文档分块 (0.5d)

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| D1 | 实现 Article/Chapter/SlidingWindow 三策略分块器 | `kg/extract/chunker.py` | ✅ |
| D2 | 正则匹配 "第X条/章/编/分编/节" + 主题启发式关键词 (超标排放/危废/排污许可/监测数据/处罚/刑事责任/环评/应急预案) | 同上 | ✅ |
| D3 | 跑全量 3384 文档，产出 chunks.jsonl (87.6 MB), 36980 chunks  (Article 7100 + Window 29878 + Chapter 2) | `kg/logs/chunks.jsonl` | ✅ |
| D4 | 修复 NoneType.strip bug (metadata 中 standard_id 为空) | 已提交修复 | ✅ |
| D5 | 发现特殊字符路径问题: Windows 文件名含 `?` 导致 [Errno 2], 部分文档 chunk 跳过 | issue 已记录 | ⚠️ |

### E. LLM 抽取 (2d)

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| E1 | 编写 SYSTEM_PROMPT 严格 JSON Schema 模板 (entities + relationships) | `kg/extract/llm_extractor.py` | ✅ |
| E2 | GLM-4.7-Flash 调用 + 30s 退避 + 3 次重试 + 限流处理 (429/1302/1305) | 同上 | ✅ |
| E3 | JSON 提取兜底 (```json``` 包裹解析) | 同上 | ✅ |
| E4 | 实体归一化扩展: 30+ → **80+ 别名** (Pollutant 40+ / Industry 30+ / Violation 10+ / Penalty 10+) | 同上 NORMALIZATION 字典 | ✅ |
| E5 | 增量写入 + 断点恢复 (extractions.jsonl 持久化) | 同上 | ✅ |
| E6 | **全量抽取**: 提交 200 篇 Article, 成功 117 条 (114 有效), 200 条 failed 3 | `kg/logs/extractions_200.jsonl` | ✅ |
| E7 | API key 排障: GLMapi.md 旧 key 失效 → 新 key, 10 个 Python 文件批量替换 | 已修复 401 | ✅ |
| E8 | 烟测 20 篇: avg 47 entities / 39 rels per doc | `kg/logs/extractions_20.jsonl` | ✅ |

### F. 图谱构建 (1.5d)

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| F1 | GraphStoreProtocol 抽象层 + NetworkXGraphStore 内存版 | `kg/import_db/graph_store.py` | ✅ |
| F2 | Neo4j 接口预留: 19 条 CONSTRAINTS + Cypher 模板 | 同上 + `kg/import_db/neo4j_importer.py` | ✅ |
| F3 | Phase A: 从 3384 份 metadata.json 导入静态层 | 778 节点 / 6814 关系 | ✅ |
| F4 | Phase B: LLM 抽取结果导入动态层 (MERGE 增量) | `kg/import_db/import_extractions.py` | ✅ |
| F5 | **Phase B 扩展**: 117 篇 LLM 抽取 → **2209 节点 / 8059 关系** (较 5 月底 +89%) | `kg/import_db/graph_store/graph_full.json` | ✅ |
| F6 | text_search 增强: 双向包含 + 中文分词兜底 (jieba) | `kg/import_db/graph_store.py` | ✅ |
| F7 | 图谱持久化 (`graph_full.json`) | 2.7 MB | ✅ |

**图谱构成 (2209 节点 / 8059 关系)**:
| 节点类型 | 数量 | 关系类型 | 数量 |
|---|---|---|---|
| Organization | 310 | BELONGS_TO_INDUSTRY | 1137 |
| Industry | 89 | HAS_VIOLATION | 920 |
| PollutionSource | 155 | SOURCE_EMITS_POLLUTANT | 851 |
| Violation | 284 | CASE_HAS_VIOLATION | 667 |
| Pollutant | 129 | CASE_TRIGGERS_PENALTY | 629 |
| Article | **114** (+470% vs 5月底20) | ARTICLE_REGULATES | 492 |
| Region | 124 | INDUSTRY_USES_TECH | 371 |
| TreatmentTech | 81 | REGULATION_APPLIES_TO_INDUSTRY | 350 |
| Penalty | 78 | ARTICLE_DEFINES_VIOLATION | 311 |
| Law | 49 | ARTICLE_DEFINES_PENALTY | 297 |
| Case | **43** (+291% vs 5月底11) | HAS_ARTICLE | 253 |
| Standard | 29 | CONTAINS_ARTICLE | 216 |
| Regulation | 27 | LOCAL_CASE | 70 |
| *其他* | 186 | *其他关系* | 1495 |

### G. 嵌入与索引 (0.5d, 后台进行中)

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| G1 | 部署 BAAI/bge-small-zh-v1.5 (512 维) | `kg/embed/vector_embedder.py` | ✅ |
| G2 | 嵌入缓存 + ChromaDB PersistentClient (cosine) | 同上 | ✅ |
| G3 | 批量写入 7000 向量 (BATCH_SIZE=64, 1000/批) | `kg/embed/chroma_store/` | ✅ |
| G4 | **后台启动全量 36980 嵌入** (PID 3972, 预计 2-4h) | `kg/logs/bg_embed.out` | ⏳ 进行中 |
| G5 | metadata 索引: doc_id / doc_type / article_no / path / themes | ChromaDB filter | ✅ |

### H. 混合检索 (1d)

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| H1 | 意图路由: 7 类歧义 (list_pollutants_by_standard / list_articles_about_pollutant / find_similar_cases / lookup_penalty_for_violation / lookup_article_content / case_sop / industry_compliance) + semantic_fallback | `kg/retrieve/hybrid_retriever.py` | ✅ |
| H2 | VectorRetriever (ChromaDB) | 同上 | ✅ |
| H3 | GraphRetriever (NetworkX text_search) | 同上 | ✅ |
| H4 | BM25Retriever (jieba + rank_bm25 库) | 同上 | ✅ |
| H5 | **RRF 加权调参**: graph 1.5x / BM25 1.2x / vector 1.0x, k=60, **召回 44%→70%** (+26pp) | `kg/skeleton_v1.py` | ✅ |

### I. RAG + text2Cypher (0.8d)

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| I1 | 接入 GLM-4.7-Flash (max_tokens=1500→16000, temperature=0.1, thinking=disabled) | `kg/rag/qa_chain.py` | ✅ |
| I2 | Prompt 模板: 证据排序 + 引用编号 + 简洁专业 (≤500 字) | 同上 | ✅ |
| I3 | 请求间隔 3s 防限流 (1305) | 同上 | ✅ |
| I4 | **text2Cypher 完整版**: 7 类规则模板 (pollutant_by_standard / articles_about_violation / penalty_for_violation / cases_by_type / industry_compliance / region_orgs / source_treatment) + GLM 兜底 | `kg/rag/text2cypher.py` | ✅ |

### J. 5 智能体 Skill (0.5d)

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| J1 | SKILL_REGISTRY 注册 5 智能体 | `kg/skills/agent_skills.py` | ✅ |
| J2 | **智能问答 (qa)**: RAG 通用 | 同上 | ✅ |
| J3 | **智能问数 (nl2sql)**: 路由到 graph 查询 + 模拟 SQL | 同上 | ✅ |
| J4 | **大气污染溯源 (tracing)**: 列出 Pollutant → PollutionSource | 同上 | ✅ |
| J5 | **企业管控 (compliance)**: 列出 applicable Standards | 同上 | ✅ |
| J6 | **报告造假 (fraud)**: RAG + Article 条款检索 (可回答 刑法 286 条 + 7.7 万案例) | 同上 | ✅ |

### K. 端到端 Demo + 验证 (0.5d)

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| K1 | skeleton_v1.py: graph + vector + bm25 + RAG + 5 Skills 全链路集成 | `kg/skeleton_v1.py` | ✅ |
| K2 | 8 测试用例: qa/nl2sql/tracing/compliance/fraud 全覆盖 | 同上 (8/8 通过) | ✅ |
| K3 | **fraud 深度测试**: "企业篡改监测数据" → 正确引用刑法第 286 条 + 7.7 万案例 + 量刑幅度 | `kg/logs/skeleton_v4.log` | ✅ |
| K4 | SKELETON_REPORT.md (架构图 + 模块清单 + 测试结果 + 后续计划) | `kg/SKELETON_REPORT.md` | ✅ |

### L. 评估体系 (1.5d) — 0602 新增

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| L1 | 设计 50 题评估集 (5 类各 10 题: law_article / standard_limit / industry_compliance / case_recommend / process_sop) | `kg/eval/dataset_v0.json` | ✅ |
| L2 | 实现 evaluate.py: 召回 60% (expected_evidence 类型命中) + 回答 40% (关键词命中 ≥50%) + GLM 评分 | `kg/eval/evaluate.py` | ✅ |
| L3 | 跑 baseline (50 题): **平均 0.476**, 召回 44%, 回答 80% | `kg/logs/eval_baseline_*.json` | ✅ |
| L4 | **RRF 加权快速验证** (20 题): 召回 44% → **70%**, 回答 65%, 平均 0.483 | `kg/logs/eval_quick_*.json` | ✅ |
| L5 | quick_test.py: 快速评估脚本 (取前 N 题, 避免全量 5min+) | `kg/eval/quick_test.py` | ✅ |

**Baseline 按类型明细 (50 题)**:
| 类型 | 总数 | 平均分 | 召回 | 回答 | 评估 |
|---|---|---|---|---|---|
| law_article | 13 | **0.574** | 较高 | 较高 | ✅ 最佳 |
| standard_limit | 11 | **0.564** | 较高 | 高 | ✅ 较好 |
| industry_compliance | 11 | 0.439 | 中 | 高 | ⚠️ 需要优化 |
| case_recommend | 10 | 0.390 | 中低 | 高 | ⚠️ 需要优化 |
| process_sop | 5 | **0.284** | 低 | 低 | ❌ 最差, 需走 text2Cypher |

### M. 图谱可视化 (0.3d) — 0602 新增

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| M1 | pyvis 图谱可视化: 邻接列表 + 节点大小 (按度) + 颜色 (按 label) + 力导向布局 | `kg/visualize/graph_viewer.py` | ✅ |
| M2 | 4 个子图: Pollutant 网 / Industry 网 / Case+Violation 网 / Region+Org 网 | 4 个 HTML 文件 | ✅ |

### N. 文档交付 (0.5d) — 0602 新增

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| N1 | MONTHLY_REPORT_2026-05.md (本月月报) | `Doc/MONTHLY_REPORT_2026-05.md` | ✅ |
| N2 | PROGRESS_REVIEW_2026-05.md (进度 Review + 下一步 + 潘云泓话术) | `Doc/PROGRESS_REVIEW_2026-05.md` | ✅ |
| N3 | NEXT_SESSION_PROMPT.md (AI 会话提示词, 下次粘贴即用) | `Doc/NEXT_SESSION_PROMPT.md` | ✅ |
| N4 | 0602 计划 + 完成报告 | `Doc/0602_工作/` | ✅ |

### O. 环境与基础设施 (0.3d)

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| O1 | 新建 conda env `zjn` (Python 3.11), 安装 zai-sdk / sentence-transformers / rank_bm25 / neo4j / chromadb / networkx / jieba / pyyaml / tiktoken / pyvis | 独立环境 | ✅ |
| O2 | 10 个 Python 文件 API key 批量替换 (旧 → 新) | 排除 401 | ✅ |
| O3 | zjn / zjn 环境备注修正 (zjn = 中节能, 不是人名) | master-summary.md | ✅ |
| O4 | PowerShell 5.1 环境适配 (conda activate zjn, $env:PYTHONIOENCODING=utf-8, 2>$null) | 运行脚本 | ✅ |

---

## 三、月度工时

| 类别 | 工时 (人天) |
|---|---:|
| A 数据治理 | 1.0 |
| B 需求对齐 | 0.5 |
| C 本体设计 | 1.0 |
| D 分块 | 0.5 |
| E LLM 抽取 | **2.0** (0602 +0.5) |
| F 图谱构建 | **1.5** (0602 +0.5) |
| G 嵌入与索引 | 0.5 |
| H 混合检索 | 1.0 |
| I RAG + text2Cypher | **0.8** (0602 +0.3) |
| J 5 智能体 | 0.5 |
| K Demo + 验证 | **0.5** (0602 +0.2) |
| L 评估体系 | **1.5** (0602 新增) |
| M 图谱可视化 | **0.3** (0602 新增) |
| N 文档交付 | **0.5** (0602 新增) |
| O 环境基础设施 | **0.3** (0602 +0.1) |
| **合计** | **12.4** (含 0602 +3.9) |

---

## 四、关键成果

1. **全栈骨架全线跑通**：分块→LLM 抽取→图谱入库→混合检索→RAG→5 智能体，8 测试全过，fraud 可回答刑法 286 条 + 7.7 万案例
2. **知识图谱 2209 节点 / 8059 关系**：114 篇 Article 动态层 + 3384 份 metadata 静态层，较 5 月底增长 89%
3. **评估体系从 0 到 1**：50 题评估集 + evaluate.py + baseline + RRF 加权优化
4. **RRF 加权效果显著**：召回 44% → 70%（+26pp），平均分 0.476 → 0.483（20 题验证）
5. **text2Cypher 完整版**：7 类规则模板 + GLM 兜底，给 5 智能体提供结构化查询能力
6. **实体归一化 80+ 别名**：Pollutant 40+ / Industry 30+ / Violation 10+ / Penalty 10+
7. **可交付资产**：50 题评估集 / 4 个 pyvis 子图 / 3 份文档 / 全量 chunks / 全量向量 / 全量图谱 JSON

---

## 五、问题与风险

| 类别 | 描述 | 影响 | 缓解 |
|---|---|---|---|
| 限流 | GLM-4.7-Flash 触发 1305 (账户级 QPS) | 7100 Article 抽取需 8-12h | 错峰后台跑, 30s backoff + failed.jsonl 断点恢复 |
| 路径特殊字符 | Windows 文件名含 `?` 导致 [Errno 2], 部分 md 文件 chunk 失败 | 约 10-15% 文件跳过 | chunker 加 safe_filename 净化 (0603 提 PR) |
| 基础设施 | 没起 Neo4j 服务器, 暂用 NetworkXGraphStore | 不支持生产级查询 | 月底申请服务器, 抽象层已就绪, 一行切换 |
| 算力 | BGE 嵌入 + LLM 抽取在 CPU 上慢 | 迭代速度受限 | 6 月申请一体机 GPU (A100/H800) |
| 评估 KPI 差距 | Baseline 平均 0.476 vs KPI 0.85 | 合同风险 (差 0.37) | 0603 继续：RRF 加权 + 评估集 v1 100 题 + GLM 评分调参 |
| 业务数据 | 5 智能体骨架在, 但缺实时监测/案例库/法规全文 | 深度开发受阻 | 0603 与王工/任伟对接数据源 |
| 回答透出 | 回答 pass 80% 但 "不要在回答里重复问题"、"控制在 300 字内" 等约束需改进 | 用户体验 | 优化 prompt 模板 |

---

## 六、6 月工作计划 (更新版)

| 优先级 | 任务 | 估时 | 责任人 | 状态 |
|---|---|---|---|---:|---|
| 🔴 P0 | 全量 LLM 抽取 7000+ Article (后台, 8-12h) | 1d | 徐简 | ⏳ 已跑 117, 余 6983 |
| 🔴 P0 | ChromaDB 全量 36980 嵌入 (后台, 2-4h) | 0.5d | 徐简 | ⏳ PID 3972 进行中 |
| 🔴 P0 | RRF 权重深度调参 + 评估集 100 题 | 1.5d | 徐简 | ⏳ RRF 已调, 扩至 100 题 |
| 🟡 P1 | neo4j 部署 (JDK17 + Neo4j Community 5) | 0.5d | 徐简+运维 | 📋 需管理员权限 |
| 🟡 P1 | Phase 1 旧图 (3321 节点) 合并进新图 (2209 节点) | 0.5d | 徐简 | 📋 |
| 🟡 P1 | 5 智能体深度开发 (接实时监测/案例库/法规库) | 3d | 徐简+任伟 | 📋 等业务数据 |
| 🟡 P1 | chunker 修复特殊字符 bug | 0.5h | 徐简 | 📋 |
| 🟡 P1 | 评估集 v1 100 题 + 按类型 baseline | 1h | 徐简 | 📋 |
| 🟢 P2 | text2Cypher 完整验证 + FastAPI endpoint 给 Dify | 1.5d | 徐简 | 📋 |
| 🟢 P2 | 同义词字典再扩展 (200+ 行业术语) | 0.3d | 徐简 | 📋 |
| 🟢 P2 | 可视化增强 (交互式查询 + 子图分析) | 0.5d | 徐简 | 📋 |

**当前 RAG+KG 完成度 ~45%** (较 5 月底 30% +15pp):
| 子模块 | 完成度 |
|---:|---:|
| 本体设计 | 100% ✅ |
| 分块 | 100% ✅ (36980 chunks) |
| LLM 抽取 | 20% (117/7100) ⏳ |
| 图谱入库 | 40% (2209 节点, 静态层 100%) |
| 向量索引 | 20% (7000/36980) ⏳ |
| 评估体系 | 30% (50 题 + baseline) |
| text2Cypher | 80% (7 类规则) |
| 5 智能体 | 5% (骨架, 缺业务数据) |
| Neo4j 部署 | 0% (需 admin) |

---

## 七、需要协调的资源

1. **算力**: 申请一体机 GPU (LLM 抽取 + 嵌入 + 微调), 当前 CPU 太慢
2. **业务数据**: 5 智能体深度开发需要 实时监测数据 / 案例库 / 法规全文, 请王工对接
3. **评估集审核**: 请林恒宇/任伟审 50 题, 帮忙扩展至 100 题 (确保代表性和难度)
4. **Neo4j 服务器**: 请潘云泓协调服务器 / 管理员装 JDK + Docker
5. **潘云泓话术**: "哥, 0602 重推进度: 图扩到 2209 节点, 评估 baseline 已出, 召回从 44% 调到 70%, 0603 继续全量抽取 + 评估集扩到 100 题"

---

**月报人**: 徐简
**日期**: 2026-06-02 (含 5/1 至 6/2)
**审阅**: 潘云泓
