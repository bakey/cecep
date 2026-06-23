# -*- coding: utf-8 -*-
"""
RAG 问答链
  - 接 HybridRetriever
  - 接 GLM-4.7 生成
  - 支持直接 Cypher 模式 (text2Cypher) 和 RAG 模式
  - 答案带引用 (源文档 + 条款 + KG 节点)
"""
import os
import re
import json
import logging
from typing import List, Dict, Optional

from zai import ZhipuAiClient

from retrieve.hybrid_retriever import HybridRetriever, route_intent

logger = logging.getLogger("kg.rag")

API_KEY = "f275cb076eab46d697c1285755ab4459.U1t2diOzRAwBYEWm"
MODEL = "glm-4.7-flash"
client = ZhipuAiClient(api_key=API_KEY)


ANSWER_PROMPT = """你是"中节能"环保执法领域 AI 助手。基于以下检索证据, 用专业、严谨的中文回答用户问题。

## 要求
1. **优先依据检索证据**, 不要编造
2. **引用具体条款**: 涉及法规时, 写明"《XXX》第X条"
3. **结构化回答**: 关键信息用列表/表格
4. **不确定时说"暂未找到明确依据"**, 不要硬编
5. **末尾给出引用**: 列出用了哪些源

## 用户问题
{query}

## 检索证据 (含图谱节点和向量片段)
{evidence}

请生成回答:
"""


def call_glm(messages: List[Dict], max_tokens: int = 4000, temperature: float = 0.2) -> Optional[str]:
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=MODEL, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
                thinking={"type": "disabled"},
            )
            return response.choices[0].message.content
        except Exception as e:
            err = str(e)
            if "429" in err or "1302" in err:
                time.sleep(30)
            else:
                logger.error(f"API 错误: {e}")
                return None
    return None


import time


class RAGChain:
    def __init__(self, retriever: HybridRetriever = None):
        self.retriever = retriever or HybridRetriever()

    def _format_evidence(self, hits: List[Dict]) -> str:
        """把检索结果格式化为 LLM 可读的 evidence"""
        lines = []
        for i, h in enumerate(hits):
            source = h.get("source", "?")
            if source == "graph" or source == "graph_text":
                node = h.get("node", {})
                if isinstance(node, dict):
                    label = list(node.keys())[:3]
                    sample = {k: str(node[k])[:200] for k in label}
                    lines.append(f"[图谱节点 {i+1}] {json.dumps(sample, ensure_ascii=False)}")
            else:
                text = h.get("text", "") or h.get("node", {}).get("content", "")
                if not text:
                    text = json.dumps(h.get("node", {}), ensure_ascii=False)
                lines.append(f"[{source} 片段 {i+1}] {text[:500]}")
        return "\n\n".join(lines) if lines else "（无证据）"

    def answer(
        self,
        query: str,
        query_embedding: List[float] = None,
        top_k: int = 8,
    ) -> Dict:
        """
        Returns: {
            "query": ...,
            "intent": [...],
            "hits": [...],
            "answer": "...",
            "citations": [...]
        }
        """
        intent = route_intent(query)
        hits = self.retriever.search(query, query_embedding=query_embedding, top_k=top_k)

        evidence = self._format_evidence(hits)
        prompt = ANSWER_PROMPT.format(query=query, evidence=evidence)

        answer = call_glm([{"role": "user", "content": prompt}])

        citations = []
        for h in hits[:5]:
            if h.get("source") in ("graph", "graph_text"):
                n = h.get("node", {})
                if isinstance(n, dict):
                    name = n.get("full_name") or n.get("name_zh") or n.get("title", "")
                    if name:
                        citations.append(f"《{name}》")
            else:
                doc_id = h.get("doc_id") or h.get("id", "")
                if doc_id:
                    citations.append(str(doc_id))

        return {
            "query": query,
            "intent": intent,
            "hits": hits,
            "answer": answer or "（生成失败）",
            "citations": citations,
        }


# -----------------------------------------------------------------------------
# text2Cypher (高级: 自然语言直接转 Cypher)
# -----------------------------------------------------------------------------
TEXT2CYPHER_PROMPT = """你是 Neo4j Cypher 专家。基于以下图谱 schema, 把用户问题转成 Cypher 查询, 只返回 Cypher 代码 (不解释)。

## Schema
节点: Law, Regulation, Standard, Article, Pollutant, PollutionSource, Industry, TreatmentTech, Region, Organization, Case, Violation, Penalty, Enterprise, Interpretation, Notice
关键属性: 
  - Pollutant.name_zh, Industry.name_zh, Region.name_zh
  - Standard.std_id (如 "GB 3095-2012")
  - Article.article_no (如 "第六条"), Article.parent_doc
  - Case.case_type, Case.case_time
关系: ISSUED_BY, APPLIES_TO_REGION, APPLIES_TO_INDUSTRY, SUPERSEDES, CONTAINS_ARTICLE, ARTICLE_REGULATES, ARTICLE_DEFINES_PENALTY, ARTICLE_DEFINES_VIOLATION, SOURCE_EMITS_POLLUTANT, STANDARD_LIMITS_POLLUTANT, CASE_VIOLATES_ARTICLE, CASE_INVOLVES_VIOLATION, CASE_TRIGGERS_PENALTY, SIMILAR_TO

## 用户问题
{query}

## Cypher
"""


def text2cypher(query: str) -> Optional[str]:
    prompt = TEXT2CYPHER_PROMPT.format(query=query)
    cypher = call_glm([{"role": "user", "content": prompt}], max_tokens=500, temperature=0.0)
    if not cypher:
        return None
    cypher = cypher.strip()
    cypher = re.sub(r"^```cypher\s*", "", cypher)
    cypher = re.sub(r"^```\s*", "", cypher)
    cypher = re.sub(r"\s*```$", "", cypher)
    return cypher


def run_text2cypher(query: str, retriever: HybridRetriever = None) -> Optional[List[Dict]]:
    """生成并执行 Cypher, 带重试"""
    retriever = retriever or HybridRetriever()
    cypher = text2cypher(query)
    if not cypher or retriever.graph._driver is None:
        return None
    try:
        with retriever.graph._driver.session() as s:
            result = s.run(cypher)
            return [dict(r) for r in result]
    except Exception as e:
        logger.error(f"Cypher 执行失败: {e}\n  CYPHER: {cypher}")
        return None


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "GB 3095-2012 的 PM2.5 限值是多少?"
    print(f">>> Q: {q}\n")
    chain = RAGChain()
    res = chain.answer(q)
    print(f"意图: {res['intent']}")
    print(f"引用: {res['citations']}")
    print(f"\n--- 答案 ---\n{res['answer']}")
