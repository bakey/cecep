# -*- coding: utf-8 -*-
"""
环保执法 RAG 骨架 v1
  - Graph: NetworkXGraphStore (从 metadata.json)
  - Vector: ChromaDB + BGE-small-zh
  - BM25: 内存 rank_bm25
  - RAG: GLM-4.7-Flash
  - Skills: 5 个智能体

测试 5 种类问:
  1. 列出 GB 3095-2012 的污染物限值
  2. 钢铁行业的适用法规
  3. SO2 相关的处罚案例
  4. 第 X 条内容
  5. 类似非法收集废机油的案例
"""
import sys
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("skeleton")

KG_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(KG_DIR.parent))

# -----------------------------------------------------------------------------
# 加载各组件
# -----------------------------------------------------------------------------
def load_graph():
    from kg.import_db.graph_store import NetworkXGraphStore
    g = NetworkXGraphStore()
    # 优先加载含 LLM 抽取的完整图
    full = KG_DIR / "import_db" / "graph_store" / "graph_full.json"
    meta_only = KG_DIR / "import_db" / "graph_store" / "graph_metadata.json"
    cache = full if full.exists() else meta_only
    if cache.exists():
        g.load(str(cache))
    else:
        from kg.import_db.graph_store import import_metadata_into_graph
        import_metadata_into_graph(g)
        g.persist(str(cache))
    return g


def load_vector():
    from kg.embed.vector_embedder import ChromaStore
    return ChromaStore()


def load_bm25(chunks_path: str, limit: int = 2000):
    chunks_p = Path(chunks_path)
    if not chunks_p.exists():
        logger.warning(f"chunks.jsonl 不存在 ({chunks_p}), BM25 不可用, 降级到 graph-only")
        return None
    from rank_bm25 import BM25Okapi
    import jieba
    docs = []
    with open(chunks_p, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            c = json.loads(line)
            docs.append({
                "id": c["chunk_id"],
                "text": c["text"],
                "doc_id": c["doc_id"],
            })
    logger.info(f"BM25 语料: {len(docs)} chunks")

    class BM25Wrap:
        def __init__(self, docs):
            self.docs = docs
            tokenized = []
            for d in docs:
                tokens = list(jieba.cut_for_search(d["text"]))
                tokenized.append(tokens)
            self.bm25 = BM25Okapi(tokenized)
        def search(self, query, top_k=10):
            tokens = list(jieba.cut_for_search(query))
            scores = self.bm25.get_scores(tokens)
            idxs = sorted(range(len(scores)), key=lambda i: -scores[i])[:top_k]
            return [
                {
                    "id": self.docs[i]["id"],
                    "text": self.docs[i]["text"],
                    "doc_id": self.docs[i]["doc_id"],
                    "score": float(scores[i]),
                    "source": "bm25",
                }
                for i in idxs if scores[i] > 0
            ]
    return BM25Wrap(docs)


def load_embedder():
    from kg.embed.vector_embedder import Embedder
    return Embedder()


# -----------------------------------------------------------------------------
# Hybrid Retriever (集成图谱 + 向量 + BM25)
# -----------------------------------------------------------------------------
class HybridRetriever:
    def __init__(self, graph, chroma, bm25, embedder):
        self.graph = graph
        self.chroma = chroma
        self.bm25 = bm25
        self.embedder = embedder

    def search(self, query: str, top_k: int = 5) -> list:
        from collections import defaultdict
        scores = defaultdict(lambda: {"rrf": 0.0, "payload": None, "sources": []})

        q_lower = query.lower()
        case_kw = ["案例", "处罚", "违法", "查处", "典型", "追究", "罚款", "拘留", "犯罪"]
        sop_kw = ["流程", "程序", "步骤", "如何", "怎么办", "怎么申请", "怎么办理", "怎么走"]
        is_case = any(kw in q_lower for kw in case_kw)
        is_sop = any(kw in q_lower for kw in sop_kw)
        graph_w = 2.0 if is_case else 1.5
        bm25_w = 1.0 if is_case else 1.8 if is_sop else 1.5

        # 1. Vector (权重 1.0)
        try:
            if self.embedder and self.chroma and self.chroma._collection and self.chroma._collection.count() > 0:
                q_emb = self.embedder.encode([query])[0]
                v_hits = self.chroma.search(q_emb, top_k=top_k * 2)
                for rank, h in enumerate(v_hits):
                    key = h["chunk_id"]
                    scores[key]["rrf"] += 1.0 / (60 + rank + 1)
                    scores[key]["payload"] = h
                    scores[key]["sources"].append("vector")
        except Exception as e:
            logger.warning(f"Vector 检索失败: {e}")

        # 2. Graph text search
        try:
            g_hits = self.graph.text_search(query, top_k=top_k * 2)
            for rank, h in enumerate(g_hits):
                node = h["node"]
                key = "graph_" + node.get("id", str(rank))
                scores[key]["rrf"] += graph_w / (60 + rank + 1)
                scores[key]["payload"] = {
                    "text": node.get("name_zh") or node.get("full_name") or node.get("summary", ""),
                    "doc_id": node.get("id", ""),
                    "label": node.get("label", ""),
                    "score": h.get("score", 0),
                    "source": "graph",
                    "node": node,
                }
                scores[key]["sources"].append("graph")
        except Exception as e:
            logger.warning(f"Graph 检索失败: {e}")

        # 3. BM25
        try:
            if self.bm25:
                b_hits = self.bm25.search(query, top_k=top_k * 2)
                for rank, h in enumerate(b_hits):
                    key = h["id"]
                    scores[key]["rrf"] += bm25_w / (60 + rank + 1)
                    h["label"] = "Article"
                    h["source"] = "bm25"
                    scores[key]["payload"] = h
                    scores[key]["sources"].append("bm25")
        except Exception as e:
            logger.warning(f"BM25 检索失败: {e}")

        ranked = sorted(scores.items(), key=lambda x: -x[1]["rrf"])
        results = []
        for k, v in ranked[:top_k]:
            p = v["payload"]
            p["rrf_score"] = v["rrf"]
            p["sources"] = v["sources"]
            results.append(p)
        return results


# -----------------------------------------------------------------------------
# RAG 链路
# -----------------------------------------------------------------------------
class RAGChain:
    def __init__(self, retriever: HybridRetriever):
        from zai import ZhipuAiClient
        self.client = ZhipuAiClient(api_key="f275cb076eab46d697c1285755ab4459.U1t2diOzRAwBYEWm")
        self.retriever = retriever

    def answer(self, question: str, top_k: int = 5, intent: str = None) -> dict:
        hits = self.retriever.search(question, top_k=top_k)
        context = self._build_context(hits)
        prompt = self._build_prompt(question, context, intent=intent)
        try:
            resp = self.client.chat.completions.create(
                model="glm-4.7-flash",
                messages=[
                    {"role": "system", "content": "你是环保执法领域专家。基于以下证据, 用专业、严谨的中文回答用户问题, 并标注引用编号。若证据不足请直说。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1500,
                temperature=0.1,
                thinking={"type": "disabled"},
            )
            answer = resp.choices[0].message.content
        except Exception as e:
            logger.error(f"GLM 调用失败: {e}")
            answer = f"[GLM 错误] {e}"

        return {
            "question": question,
            "answer": answer,
            "evidence": [
                {
                    "source": h.get("source", ""),
                    "label": h.get("label", h.get("metadata", {}).get("doc_type", "")),
                    "snippet": (h.get("text") or h.get("node", {}).get("full_name") or "")[:300],
                    "rrf_score": h.get("rrf_score", 0),
                }
                for h in hits
            ],
        }

    def _build_context(self, hits) -> str:
        parts = []
        for i, h in enumerate(hits, 1):
            text = h.get("text") or ""
            label = h.get("label", h.get("source", ""))
            sources = h.get("sources", [])
            parts.append(f"[{i}] 来源: {','.join(sources)} 类型: {label} 评分: {h.get('rrf_score', 0):.4f}\n{text[:600]}")
        return "\n\n".join(parts)

    def _build_prompt(self, question, context, intent=None) -> str:
        return f"""## 证据 (按相关性排序)
{context}

## 用户问题
{question}

## 回答要求
- 引用证据编号 [1] [2] 等
- 简洁专业, 不超过 500 字
- 若证据不足明确说明

请回答:"""


# -----------------------------------------------------------------------------
# 5 个 Skill
# -----------------------------------------------------------------------------
SKILL_REGISTRY = {}


def register_skill(name):
    def deco(fn):
        SKILL_REGISTRY[name] = fn
        return fn
    return deco


@register_skill("qa")
def skill_qa(rag: RAGChain, params: dict) -> dict:
    """专业问答"""
    return rag.answer(params.get("question", ""), top_k=params.get("top_k", 5))


@register_skill("nl2sql")
def skill_nl2sql(rag: RAGChain, params: dict) -> dict:
    """智能问数 - 简化版: 路由到 graph 查询"""
    q = params.get("question", "")
    hits = rag.retriever.graph.text_search(q, top_k=5)
    rows = []
    for h in hits:
        n = h["node"]
        rows.append({
            "label": n.get("label"),
            "name_zh": n.get("name_zh"),
            "full_name": n.get("full_name"),
            "summary": n.get("summary", "")[:120],
        })
    return {"question": q, "rows": rows, "sql": f"-- graph: MATCH (n) WHERE n.full_name CONTAINS '{q[:30]}' RETURN n LIMIT 5"}


@register_skill("tracing")
def skill_tracing(rag: RAGChain, params: dict) -> dict:
    """大气污染溯源 - 列出污染源/污染物/标准"""
    q = params.get("question", "")
    hits = rag.retriever.graph.text_search(q, top_k=10)
    pollutants = [h for h in hits if h["node"].get("label") == "Pollutant"]
    sources = [h for h in hits if h["node"].get("label") == "PollutionSource"]
    return {
        "question": q,
        "pollutants": [h["node"].get("name_zh") for h in pollutants],
        "sources": [h["node"].get("name_zh") for h in sources],
    }


@register_skill("compliance")
def skill_compliance(rag: RAGChain, params: dict) -> dict:
    """企业合规检查"""
    q = params.get("question", "")
    hits = rag.retriever.graph.text_search(q, top_k=10)
    standards = [h for h in hits if h["node"].get("label") == "Standard"]
    return {
        "question": q,
        "applicable_standards": [h["node"].get("full_name") for h in standards[:5]],
    }


@register_skill("fraud")
def skill_fraud(rag: RAGChain, params: dict) -> dict:
    """报告造假识别 - 检索监测/数据/虚假相关条款"""
    q = params.get("question", "")
    hits = rag.retriever.search(q, top_k=5)
    return rag.answer(q, top_k=5, intent="fraud")


def call_skill(name: str, rag: RAGChain, params: dict) -> dict:
    if name not in SKILL_REGISTRY:
        return {"error": f"未知 Skill: {name}, 已注册: {list(SKILL_REGISTRY)}"}
    return SKILL_REGISTRY[name](rag, params)


# -----------------------------------------------------------------------------
# 演示
# -----------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("环保执法 RAG 骨架 v1")
    print("=" * 70)

    print("\n[1/4] 加载图谱...")
    graph = load_graph()
    stats = graph.stats()
    print(f"  图谱: {stats['nodes_total']} 节点, {stats['edges_total']} 关系")
    print(f"  节点类型: {stats['by_label']}")

    print("\n[2/4] 加载向量库 (ChromaDB)...")
    chroma = load_vector()
    n = chroma._collection.count() if chroma._collection else 0
    print(f"  ChromaDB: {n} 向量")

    print("\n[3/4] 加载 BM25 + Embedder...")
    chunks_path = str(KG_DIR / "logs" / "chunks.jsonl")
    bm25 = load_bm25(chunks_path, limit=2000)
    embedder = load_embedder()
    print(f"  BM25: {len(bm25.docs)} 文档, Embedder: {embedder.dim} 维")

    print("\n[4/4] 初始化 RAG + 5 Skills...")
    retriever = HybridRetriever(graph, chroma, bm25, embedder)
    rag = RAGChain(retriever)
    print(f"  已注册 Skills: {list(SKILL_REGISTRY)}")

    # ------------------- 测试 -------------------
    test_queries = [
        ("nl2sql", "查询河北省的所有地方标准"),
        ("tracing", "SO2 的主要污染源"),
        ("qa", "中华人民共和国水污染防治法的处罚条款"),
        ("fraud", "监测数据造假有什么处罚？"),
        ("compliance", "河北省 地方 标准"),
        ("qa", "危险废物相关的处罚规定"),
        ("tracing", "工业废水的主要污染物"),
        ("qa", "大气污染排放标准"),
    ]

    import time as _time
    for i, (skill, q) in enumerate(test_queries, 1):
        print(f"\n{'=' * 70}")
        print(f"测试 {i}/{len(test_queries)}: [{skill}] {q}")
        print(f"{'=' * 70}")
        try:
            result = call_skill(skill, rag, {"question": q})
            if "answer" in result:
                print(f"\n[回答]\n{result['answer'][:600]}")
                print(f"\n[证据数] {len(result.get('evidence', []))}")
                for ev in result.get("evidence", [])[:3]:
                    print(f"  - {ev['source']}/{ev['label']} (rrf={ev['rrf_score']:.3f}): {ev['snippet'][:100]}")
            elif "rows" in result:
                print(f"\n[SQL] {result.get('sql', '')[:200]}")
                for r in result.get("rows", [])[:5]:
                    print(f"  - {r.get('label', '')}: {r.get('full_name') or r.get('name_zh') or r.get('summary', '')[:80]}")
            elif "applicable_standards" in result:
                for s in result.get("applicable_standards", [])[:5]:
                    print(f"  - {s}")
            elif "pollutants" in result:
                print(f"  污染物: {result['pollutants']}")
                print(f"  污染源: {result['sources']}")
            else:
                print(f"  {json.dumps(result, ensure_ascii=False, indent=2)[:400]}")
        except Exception as e:
            logger.error(f"Skill {skill} 失败: {e}")
            import traceback
            traceback.print_exc()
        _time.sleep(3)  # 限流保护

    print(f"\n{'=' * 70}")
    print("骨架 v1 演示完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
