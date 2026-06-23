# -*- coding: utf-8 -*-
"""
混合检索器 (Hybrid Retriever)
  - 向量检索 (BGE / m3e, 部署在 ChromaDB 或 Neo4j vector index)
  - 图谱检索 (Cypher 模板 + text2Cypher)
  - 全文检索 (BM25)
  - 三路召回 + RRF 融合
"""
import os
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

import math

logger = logging.getLogger("kg.retriever")
logger.setLevel(logging.INFO)

KG_DIR = Path(__file__).resolve().parent.parent

# -----------------------------------------------------------------------------
# 意图路由 (Router)
#   - 根据查询判断走哪条检索路径
# -----------------------------------------------------------------------------
INTENT_PATTERNS = {
    "list_pollutants_by_standard": [
        r"(GB|HJ|DB)\s*\d+",
        r"标准.*?(限值|浓度|排放)",
        r"排放限值",
    ],
    "list_articles_about_pollutant": [
        r"(SO2|NOx|PM2\.5|PM10|VOCs|COD|氨氮|总磷|危险废物|危废)",
        r"(大气|水|土壤).{0,5}(污染|防治)",
    ],
    "find_similar_cases": [
        r"(类似|相似).*?(案例|案件|判例)",
        r"案[例例]",
        r"参考",
    ],
    "lookup_penalty_for_violation": [
        r"(处罚|罚|刑)",
        r"(超标|违法).*?(罚款|拘留|停产)",
    ],
    "lookup_article_content": [
        r"第[一二三四五六七八九十百千零〇0-9]+条",
        r"(说什么|怎么规定|怎么罚|怎么处罚)",
    ],
    "case_sop": [
        r"核查流程|SOP|怎么办|怎么查|如何处置",
    ],
    "industry_compliance": [
        r"(钢铁|化工|印染|造纸|火电|水泥|制药).*?(合规|标准|要求)",
    ],
}


def route_intent(query: str) -> List[str]:
    """返回命中的意图列表, 按相关性排序"""
    scored = []
    for intent, patterns in INTENT_PATTERNS.items():
        score = sum(1 for p in patterns if re.search(p, query, re.IGNORECASE))
        if score > 0:
            scored.append((score, intent))
    scored.sort(reverse=True)
    return [it for _, it in scored] or ["semantic_fallback"]


# -----------------------------------------------------------------------------
# Vector 检索 (ChromaDB / Neo4j Vector)
# -----------------------------------------------------------------------------
class VectorRetriever:
    """ChromaDB 抽象, 支持离线降级 (无 Chroma 时直接 None)"""

    def __init__(self, persist_dir: str = None, collection_name: str = "kg_chunks"):
        self.persist_dir = persist_dir or str(KG_DIR / "embed" / "chroma_store")
        self.collection_name = collection_name
        self._client = None
        self._collection = None
        try:
            import chromadb
            from chromadb.config import Settings
            self._client = chromadb.PersistentClient(path=self.persist_dir)
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            logger.info(f"ChromaDB 加载: {self.persist_dir} (n={self._collection.count()})")
        except Exception as e:
            logger.warning(f"ChromaDB 不可用: {e}, 降级为 None")

    def add(self, ids: List[str], texts: List[str], metadatas: List[Dict], embeddings: List[List[float]] = None):
        if self._collection is None:
            return
        self._collection.add(
            ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings
        )

    def search(self, query_embedding: List[float], top_k: int = 10) -> List[Dict]:
        if self._collection is None:
            return []
        res = self._collection.query(
            query_embeddings=[query_embedding], n_results=top_k
        )
        hits = []
        for i, (doc, meta, dist) in enumerate(zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        )):
            hits.append({
                "doc_id": meta.get("doc_id"),
                "chunk_id": res["ids"][0][i],
                "text": doc,
                "score": 1 - dist,  # cosine distance → similarity
                "metadata": meta,
                "source": "vector",
            })
        return hits


# -----------------------------------------------------------------------------
# 图谱检索 (Cypher 模板)
# -----------------------------------------------------------------------------
class GraphRetriever:
    """Neo4j 抽象"""

    CYPHER_TEMPLATES = {
        "list_pollutants_by_standard": """
            MATCH (s:Standard {std_id: $std_id})
            OPTIONAL MATCH (s)-[:STANDARD_LIMITS_POLLUTANT]->(p:Pollutant)
            RETURN s.full_name AS standard, collect(DISTINCT p.name_zh) AS pollutants
        """,
        "list_articles_about_pollutant": """
            MATCH (p:Pollutant {name_zh: $pollutant})<-[r:STANDARD_LIMITS_POLLUTANT|ARTICLE_REGULATES]-(a)
            WHERE a:Article OR a:Standard OR a:Law OR a:Regulation
            OPTIONAL MATCH (parent)<-[:CONTAINS_ARTICLE]-(a) WHERE a:Article
            RETURN a, p
            LIMIT 20
        """,
        "find_similar_cases": """
            MATCH (c:Case)
            WHERE c.case_type CONTAINS $case_type OR c.violations CONTAINS $vio_keyword
            OPTIONAL MATCH (c)-[:CASE_VIOLATES_ARTICLE]->(art:Article)
            OPTIONAL MATCH (c)-[:CASE_TRIGGERS_PENALTY]->(pen:Penalty)
            RETURN c, collect(DISTINCT art.article_no) AS articles, collect(DISTINCT pen.name_zh) AS penalties
            LIMIT 10
        """,
        "lookup_article_content": """
            MATCH (a:Article)
            WHERE a.article_no = $article_no AND a.parent_doc CONTAINS $doc_keyword
            RETURN a
            LIMIT 5
        """,
        "industry_compliance": """
            MATCH (i:Industry {name_zh: $industry})
            OPTIONAL MATCH (i)-[:APPLIES_TO_INDUSTRY]-(reg)
            WHERE reg:Standard OR reg:Regulation
            OPTIONAL MATCH (reg)-[:CONTAINS_ARTICLE]->(art:Article)
            RETURN i, collect(DISTINCT reg.full_name) AS regulations, collect(DISTINCT art.article_no) AS articles
            LIMIT 20
        """,
    }

    def __init__(self, uri: str = None, user: str = None, password: str = None):
        self.uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.environ.get("NEO4J_USER", "neo4j")
        self.password = password or os.environ.get("NEO4J_PASSWORD", "neo4j1234")
        self._driver = None
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            with self._driver.session() as s:
                s.run("RETURN 1")
            logger.info(f"Neo4j 已连接: {self.uri}")
        except Exception as e:
            logger.warning(f"Neo4j 不可用: {e}")
            self._driver = None

    def search(self, intent: str, params: Dict) -> List[Dict]:
        if self._driver is None or intent not in self.CYPHER_TEMPLATES:
            return []
        cypher = self.CYPHER_TEMPLATES[intent]
        try:
            with self._driver.session() as s:
                result = s.run(cypher, **params)
                return [dict(r) for r in result]
        except Exception as e:
            logger.error(f"Cypher 执行失败 [{intent}]: {e}")
            return []

    def text_search(self, query: str, top_k: int = 10) -> List[Dict]:
        """全文兜底: 任何节点 name_zh / full_name / content 匹配"""
        if self._driver is None:
            return []
        cypher = """
            MATCH (n)
            WHERE n.name_zh CONTAINS $q OR n.full_name CONTAINS $q
               OR (n:Article AND n.content CONTAINS $q)
            RETURN n, labels(n) AS labels
            LIMIT $k
        """
        try:
            with self._driver.session() as s:
                result = s.run(cypher, q=query, k=top_k)
                return [{"node": dict(r["n"]), "labels": r["labels"]} for r in result]
        except Exception as e:
            logger.error(f"text_search 失败: {e}")
            return []


# -----------------------------------------------------------------------------
# BM25 全文检索 (无依赖, 内存实现)
# -----------------------------------------------------------------------------
class BM25Retriever:
    """简单 BM25, 适用于小语料 (< 100K 文档)"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = []
        self.doc_ids = []
        self.avgdl = 0
        self.df = defaultdict(int)
        self.tf = []
        self.idf = {}
        self.N = 0

    def fit(self, docs: List[Dict]):
        """docs: [{'id': str, 'text': str}]"""
        self.docs = docs
        self.doc_ids = [d["id"] for d in docs]
        self.N = len(docs)
        total_len = 0
        for d in docs:
            tokens = self._tokenize(d["text"])
            self.tf.append(self._term_freq(tokens))
            total_len += len(tokens)
            for term in set(tokens):
                self.df[term] += 1
        self.avgdl = total_len / max(1, self.N)
        self.idf = {term: math.log((self.N - df + 0.5) / (df + 0.5) + 1) for term, df in self.df.items()}

    def _tokenize(self, text: str) -> List[str]:
        text = text.lower()
        # 中英混合分词 (粗): 切出 ASCII 词 + 单汉字
        tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fa5]", text)
        return tokens

    def _term_freq(self, tokens: List[str]) -> Dict[str, int]:
        tf = defaultdict(int)
        for t in tokens:
            tf[t] += 1
        return tf

    def search(self, query: str, top_k: int = 10) -> List[Dict]:
        if not self.docs:
            return []
        q_tokens = self._tokenize(query)
        scores = []
        for i, tf in enumerate(self.tf):
            doc_len = sum(tf.values())
            s = 0
            for t in q_tokens:
                if t in tf:
                    idf = self.idf.get(t, 0)
                    tfv = tf[t]
                    s += idf * (tfv * (self.k1 + 1)) / (tfv + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl))
            scores.append((s, i))
        scores.sort(reverse=True)
        return [
            {"id": self.doc_ids[i], "text": self.docs[i]["text"], "score": s, "source": "bm25"}
            for s, i in scores[:top_k] if s > 0
        ]


# -----------------------------------------------------------------------------
# Reciprocal Rank Fusion
# -----------------------------------------------------------------------------
def rrf_fuse(rank_lists: List[List[Dict]], k: int = 60) -> List[Dict]:
    """Reciprocal Rank Fusion: score(d) = sum(1 / (k + rank(d)))"""
    fused_score = defaultdict(float)
    payload = {}
    for lst in rank_lists:
        for rank, hit in enumerate(lst):
            doc_id = hit.get("id") or hit.get("chunk_id") or hit.get("doc_id") or json.dumps(hit, sort_keys=True)
            fused_score[doc_id] += 1.0 / (k + rank + 1)
            if doc_id not in payload:
                payload[doc_id] = hit
    ranked = sorted(fused_score.items(), key=lambda x: -x[1])
    return [{**payload[d], "rrf_score": s} for d, s in ranked]


# -----------------------------------------------------------------------------
# 主检索器
# -----------------------------------------------------------------------------
class HybridRetriever:
    def __init__(self, vector: VectorRetriever = None, graph: GraphRetriever = None, bm25: BM25Retriever = None):
        self.vector = vector or VectorRetriever()
        self.graph = graph or GraphRetriever()
        self.bm25 = bm25

    def search(
        self,
        query: str,
        query_embedding: List[float] = None,
        top_k: int = 10,
    ) -> List[Dict]:
        """
        返回融合后的 top_k 结果
        每条结果: {"id": ..., "text": ..., "score": float, "source": "vector|graph|bm25", ...}
        """
        intent = route_intent(query)
        logger.info(f"路由意图: {intent}")

        rank_lists = []

        # 1. Vector
        if query_embedding and self.vector._collection is not None:
            try:
                v_hits = self.vector.search(query_embedding, top_k=top_k)
                rank_lists.append(v_hits)
            except Exception as e:
                logger.warning(f"Vector 检索失败: {e}")

        # 2. Graph (按 intent 选 Cypher)
        graph_hits = []
        for it in intent[:2]:  # 取前两个意图
            params = self._extract_params(query, it)
            if params:
                res = self.graph.search(it, params)
                for r in res:
                    if "n" in r or "a" in r:
                        node = r.get("n") or r.get("a") or r.get("c")
                        if node is not None:
                            graph_hits.append({
                                "id": dict(node).get("id") or dict(node).get("name_zh") or dict(node).get("full_name"),
                                "node": dict(node),
                                "score": 1.0,
                                "source": "graph",
                            })
        if not graph_hits:
            # 兜底全文
            graph_hits = self.graph.text_search(query, top_k=top_k)
            graph_hits = [{"id": h["node"].get("name_zh", ""), "node": h["node"], "score": 0.5, "source": "graph_text"} for h in graph_hits]
        rank_lists.append(graph_hits)

        # 3. BM25
        if self.bm25 and self.bm25.docs:
            rank_lists.append(self.bm25.search(query, top_k=top_k))

        # 4. RRF 融合
        fused = rrf_fuse(rank_lists)
        return fused[:top_k]

    def _extract_params(self, query: str, intent: str) -> Optional[Dict]:
        if intent == "list_pollutants_by_standard":
            m = re.search(r"((?:GB|HJ|DB)\s*[\d\-—]+)", query, re.IGNORECASE)
            if m:
                return {"std_id": m.group(1).replace(" ", "")}
        if intent == "list_articles_about_pollutant":
            m = re.search(r"(SO2|NOx|PM2\.5|PM10|VOCs|COD|氨氮|总磷|危险废物|危废)", query, re.IGNORECASE)
            if m:
                return {"pollutant": m.group(1)}
        if intent == "lookup_article_content":
            m = re.search(r"第([一二三四五六七八九十百千零〇0-9]+)条", query)
            if m:
                return {"article_no": "第" + m.group(1) + "条", "doc_keyword": query}
        if intent == "industry_compliance":
            m = re.search(r"(钢铁|化工|印染|造纸|火电|水泥|制药|石化|焦化|冶炼)", query)
            if m:
                return {"industry": m.group(1)}
        if intent == "find_similar_cases":
            return {"case_type": query, "vio_keyword": query}
        return None


# -----------------------------------------------------------------------------
# 单元测试
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    r = HybridRetriever()
    test_qs = [
        "GB 3095-2012 里 PM2.5 的限值是多少?",
        "钢铁行业有哪些适用标准?",
        "危险废物的判罚依据是什么?",
        "类似非法收集废机油的案例",
    ]
    for q in test_qs:
        print(f"\n>>> Q: {q}")
        try:
            res = r.search(q, top_k=5)
            for h in res[:3]:
                print(f"   [{h.get('source')}] rrf={h.get('rrf_score', 0):.3f} - {str(h.get('id', ''))[:50]}")
        except Exception as e:
            print(f"   失败: {e}")
