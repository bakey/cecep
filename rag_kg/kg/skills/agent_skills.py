# -*- coding: utf-8 -*-
"""
5 个智能体的 KG Skill 适配层
每个 Skill = 一个 Python 函数/类, 封装:
  - 该智能体关注哪些图谱子图
  - 推荐的 Cypher 模板
  - 返回结构化数据 (供前端/Agent 编排使用)

5 个智能体:
  1. 智能问答智能体 (qa_skill)
  2. 智能问数智能体 (nl2sql_skill)
  3. 大气污染溯源智能体 (tracing_skill)
  4. 企业管控分析智能体 (compliance_skill)
  5. 自行监测报告造假分析智能体 (fraud_skill)
"""
import os
import re
import json
import logging
from typing import List, Dict, Optional
from pathlib import Path

from retrieve.hybrid_retriever import GraphRetriever, HybridRetriever
from rag.qa_chain import RAGChain

logger = logging.getLogger("kg.skills")
KG_DIR = Path(__file__).resolve().parent.parent


def _get_driver():
    """获取 Neo4j driver (延迟连接)"""
    from retrieve.hybrid_retriever import GraphRetriever
    return GraphRetriever()._driver


def _run_cypher(cypher: str, **params) -> List[Dict]:
    driver = _get_driver()
    if driver is None:
        return []
    with driver.session() as s:
        return [dict(r) for r in s.run(cypher, **params)]


# =============================================================================
# Skill 1: 智能问答 - 法规/案例/标准咨询
# =============================================================================
def qa_skill(query: str) -> Dict:
    """
    智能问答: 政策解读 / 法规条款 / 案例推荐
    Returns: {answer, citations, intent, sub_results}
    """
    chain = RAGChain()
    res = chain.answer(query)
    return {
        "skill": "qa",
        "query": query,
        "answer": res["answer"],
        "citations": res["citations"],
        "intent": res["intent"],
        "evidence_count": len(res["hits"]),
    }


# =============================================================================
# Skill 2: 智能问数 - NL2SQL (KG 提供 Schema 路由)
# =============================================================================
def nl2sql_skill(
    query: str,
    data_domains: List[str] = None,
) -> Dict:
    """
    智能问数: 自然语言 → SQL
    KG 的作用:
      - 通过 INDUSTRY 节点路由到对应数据域/宽表
      - 通过 Pollutant 节点反查字段名
      - 通过 Article 节点辅助构造 WHERE 条件 (如"超标")
    """
    # Step 1: 路由数据域
    domain_routing_cypher = """
    UNWIND $domains AS dom_name
    MATCH (i:Industry {name_zh: dom_name})
    OPTIONAL MATCH (i)-[:APPLIES_TO_INDUSTRY]-(reg)
    WHERE reg:Standard OR reg:Regulation
    RETURN i.name_zh AS industry,
           collect(DISTINCT reg.full_name) AS applicable_regulations
    """
    domain_info = []
    if data_domains:
        domain_info = _run_cypher(domain_routing_cypher, domains=data_domains)

    # Step 2: 提取污染物 / 关键实体
    entity_cypher = """
    MATCH (p:Pollutant)
    WHERE p.name_zh IN $pollutant_names
    OPTIONAL MATCH (s:Standard)-[:STANDARD_LIMITS_POLLUTANT]->(p)
    RETURN p.name_zh AS pollutant, collect(s.std_id) AS standards
    """
    pollutants = re.findall(
        r"(SO2|NOx|PM2\.5|PM10|VOCs|COD|氨氮|总磷|总氮|危险废物|HW|O3|TSP)",
        query, re.IGNORECASE
    )
    entity_info = []
    if pollutants:
        entity_info = _run_cypher(entity_cypher, pollutant_names=list(set(pollutants)))

    # Step 3: 构造 NL2SQL 提示
    return {
        "skill": "nl2sql",
        "query": query,
        "domain_routing": domain_info,
        "entity_resolution": entity_info,
        "sql_hint": {
            "data_domains": data_domains or [],
            "pollutants": list(set(pollutants)),
            "schema_suggestion": _suggest_schema(data_domains or [], pollutants),
        },
    }


def _suggest_schema(domains: List[str], pollutants: List[str]) -> Dict:
    """根据路由结果, 建议宽表/字段"""
    return {
        "likely_tables": [f"dwd_{d}_wide" for d in domains] if domains else ["dwd_default_wide"],
        "likely_fields": pollutants + ["emission_value", "limit_value", "exceed_flag", "monitor_time"],
    }


# =============================================================================
# Skill 3: 大气污染溯源 - 找污染源 + 行业 + 案例
# =============================================================================
def tracing_skill(
    pollutant: str = "PM2.5",
    region: str = None,
    time_window: str = None,
) -> Dict:
    """
    大气污染溯源:
      - 该污染物由哪些污染源排放
      - 涉及哪些行业
      - 历史上类似溯源案例
    """
    sources_cypher = """
    MATCH (p:Pollutant {name_zh: $pollutant})<-[:SOURCE_EMITS_POLLUTANT]-(src)
    RETURN src.name_zh AS source, labels(src) AS labels
    """
    industries_cypher = """
    MATCH (p:Pollutant {name_zh: $pollutant})<-[:SOURCE_EMITS_POLLUTANT]-(i:Industry)
    OPTIONAL MATCH (i)-[:APPLIES_TO_INDUSTRY]-(reg:Standard)
    RETURN i.name_zh AS industry, collect(DISTINCT reg.std_id) AS standards
    """
    cases_cypher = """
    MATCH (c:Case)
    WHERE c.case_type CONTAINS $pollutant OR c.summary CONTAINS $pollutant
    RETURN c.title AS title, c.case_type AS type, c.summary AS summary
    LIMIT 10
    """
    return {
        "skill": "tracing",
        "pollutant": pollutant,
        "region": region,
        "time_window": time_window,
        "pollution_sources": _run_cypher(sources_cypher, pollutant=pollutant),
        "related_industries": _run_cypher(industries_cypher, pollutant=pollutant),
        "similar_cases": _run_cypher(cases_cypher, pollutant=pollutant),
    }


# =============================================================================
# Skill 4: 企业管控分析 - 找适用法规 + 历史违规 + 案例
# =============================================================================
def compliance_skill(
    industry: str,
    region: str = None,
) -> Dict:
    """
    企业管控分析:
      - 该行业适用标准
      - 行业历史违规案例
      - 典型处罚措施
    """
    standards_cypher = """
    MATCH (i:Industry {name_zh: $industry})
    OPTIONAL MATCH (i)-[:APPLIES_TO_INDUSTRY]-(reg)
    WHERE reg:Standard OR reg:Regulation
    RETURN reg.full_name AS name, reg.std_id AS std_id
    """
    cases_cypher = """
    MATCH (c:Case)-[:CASE_INVOLVES_VIOLATION]->(v:Violation)
    WHERE c.summary CONTAINS $industry OR c.title CONTAINS $industry
    OPTIONAL MATCH (c)-[:CASE_TRIGGERS_PENALTY]->(p:Penalty)
    RETURN c.title AS case_title, v.name_zh AS violation, p.name_zh AS penalty
    LIMIT 15
    """
    articles_cypher = """
    MATCH (i:Industry {name_zh: $industry})
    OPTIONAL MATCH (reg)-[:CONTAINS_ARTICLE]->(a:Article)
    WHERE (reg:Standard OR reg:Regulation) AND (reg)-[:APPLIES_TO_INDUSTRY]-(i)
    RETURN a.article_no AS article_no, a.themes AS themes, a.penalty AS penalty
    LIMIT 30
    """
    return {
        "skill": "compliance",
        "industry": industry,
        "region": region,
        "applicable_standards": _run_cypher(standards_cypher, industry=industry),
        "violation_cases": _run_cypher(cases_cypher, industry=industry),
        "relevant_articles": _run_cypher(articles_cypher, industry=industry),
    }


# =============================================================================
# Skill 5: 自行监测报告造假分析 - 找异常模式 + 类似造假案例
# =============================================================================
def fraud_skill(
    data_pattern: str,
    industry: str = None,
) -> Dict:
    """
    报告造假分析:
      - 类似异常数据模式的历史案例
      - 涉及的法规条款 (提供核查依据)
      - 推荐的核查 SOP
    """
    patterns_cypher = """
    MATCH (c:Case)
    WHERE c.summary CONTAINS $pattern OR c.title CONTAINS $pattern
    OPTIONAL MATCH (c)-[:CASE_VIOLATES_ARTICLE]->(a:Article)
    OPTIONAL MATCH (c)-[:CASE_TRIGGERS_PENALTY]->(p:Penalty)
    RETURN c.title, c.summary, collect(DISTINCT a.article_no) AS articles,
           collect(DISTINCT p.name_zh) AS penalties
    LIMIT 10
    """
    violation_cypher = """
    MATCH (v:Violation)
    WHERE v.name_zh CONTAINS '伪造' OR v.name_zh CONTAINS '篡改'
       OR v.name_zh CONTAINS '虚假' OR v.name_zh CONTAINS '造假'
    RETURN v.name_zh AS violation
    """
    return {
        "skill": "fraud",
        "pattern": data_pattern,
        "industry": industry,
        "similar_cases": _run_cypher(patterns_cypher, pattern=data_pattern),
        "known_fraud_types": _run_cypher(violation_cypher),
    }


# =============================================================================
# Skill 注册表 (供 Dify / 智能体编排平台调用)
# =============================================================================
SKILL_REGISTRY = {
    "qa": {
        "function": qa_skill,
        "description": "环保法规/政策/案例咨询",
        "input_schema": {"query": "string"},
        "output_schema": {"answer": "string", "citations": "list[string]"},
    },
    "nl2sql": {
        "function": nl2sql_skill,
        "description": "自然语言转 SQL, KG 提供数据域路由",
        "input_schema": {"query": "string", "data_domains": "list[string]?"},
        "output_schema": {"sql_hint": "dict"},
    },
    "tracing": {
        "function": tracing_skill,
        "description": "大气污染溯源 (污染源 + 行业 + 历史案例)",
        "input_schema": {"pollutant": "string", "region": "string?"},
        "output_schema": {"pollution_sources": "list", "related_industries": "list"},
    },
    "compliance": {
        "function": compliance_skill,
        "description": "企业管控分析 (适用标准 + 违规案例 + 处罚)",
        "input_schema": {"industry": "string", "region": "string?"},
        "output_schema": {"applicable_standards": "list", "violation_cases": "list"},
    },
    "fraud": {
        "function": fraud_skill,
        "description": "报告造假分析 (异常模式 + 类似案例 + 法规依据)",
        "input_schema": {"data_pattern": "string", "industry": "string?"},
        "output_schema": {"similar_cases": "list", "known_fraud_types": "list"},
    },
}


def call_skill(skill_name: str, **kwargs) -> Dict:
    if skill_name not in SKILL_REGISTRY:
        return {"error": f"Unknown skill: {skill_name}", "available": list(SKILL_REGISTRY.keys())}
    try:
        return SKILL_REGISTRY[skill_name]["function"](**kwargs)
    except Exception as e:
        return {"skill": skill_name, "error": str(e)}


# =============================================================================
# CLI 测试
# =============================================================================
if __name__ == "__main__":
    print("=== Skill 1: QA ===")
    print(json.dumps(call_skill("qa", query="危险废物非法收集怎么处罚?"), ensure_ascii=False, indent=2)[:500])

    print("\n=== Skill 2: NL2SQL ===")
    print(json.dumps(call_skill("nl2sql", query="石家庄钢铁行业 2024 年 SO2 月均排放量", data_domains=["钢铁"]), ensure_ascii=False, indent=2)[:500])

    print("\n=== Skill 3: Tracing ===")
    print(json.dumps(call_skill("tracing", pollutant="PM2.5", region="石家庄"), ensure_ascii=False, indent=2)[:500])

    print("\n=== Skill 4: Compliance ===")
    print(json.dumps(call_skill("compliance", industry="钢铁"), ensure_ascii=False, indent=2)[:500])

    print("\n=== Skill 5: Fraud ===")
    print(json.dumps(call_skill("fraud", data_pattern="监测值与工艺理论值偏差", industry="化工"), ensure_ascii=False, indent=2)[:500])
