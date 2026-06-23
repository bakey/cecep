# -*- coding: utf-8 -*-
"""
text2Cypher: 自然语言问题 → Cypher 查询
策略:
  1. 规则模板匹配 (快路径, 7 类意图)
  2. GLM-4.7-Fallback 兜底 (慢路径, 自由生成)

支持后端: NetworkXGraphStore (自定义 protocol) / Neo4j (原生)
"""
import re
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger("kg.text2cypher")

TEMPLATE_RULES = {
    # 列出标准中所有污染物
    r"(?:GB|HJ|DB)\s*[\d\-—]+|标准.*?(限值|污染物|排放)": {
        "cypher": """
            MATCH (s:Standard)
            WHERE s.std_id CONTAINS $std_id OR s.full_name CONTAINS $std_id
            OPTIONAL MATCH (s)-[:STANDARD_LIMITS_POLLUTANT]->(p:Pollutant)
            RETURN s.full_name AS standard,
                   collect(DISTINCT p.name_zh) AS pollutants
            LIMIT 20
        """,
        "extract": ["std_id"],
    },
    # 污染物 → 相关条款
    r"(SO2|NOx|PM2\.5|PM10|VOCs|COD|NH3-N|危废|危险废物)": {
        "cypher": """
            MATCH (p:Pollutant {name_zh: $pollutant})
            OPTIONAL MATCH (a)-[r:ARTICLE_REGULATES|STANDARD_LIMITS_POLLUTANT]->(p)
            WHERE a:Article OR a:Standard
            RETURN p.name_zh AS pollutant,
                   collect(DISTINCT a.article_no)[0..10] AS articles,
                   collect(DISTINCT a.full_name)[0..5] AS standards
            LIMIT 20
        """,
        "extract": ["pollutant"],
    },
    # 行业 → 适用标准
    r"(钢铁|化工|印染|造纸|火电|水泥|制药|石化|焦化|有色|电镀|电池|畜禽)": {
        "cypher": """
            MATCH (i:Industry {name_zh: $industry})
            OPTIONAL MATCH (reg)-[:APPLIES_TO_INDUSTRY]->(i)
            WHERE reg:Standard OR reg:Regulation
            RETURN i.name_zh AS industry,
                   collect(DISTINCT reg.full_name) AS standards
            LIMIT 20
        """,
        "extract": ["industry"],
    },
    # 案件 → 关联条款 + 处罚
    r"(案件|案例|参考|类似)": {
        "cypher": """
            MATCH (c:Case)
            WHERE c.title CONTAINS $keyword OR c.case_type CONTAINS $keyword
            OPTIONAL MATCH (c)-[:CASE_VIOLATES_ARTICLE]->(art:Article)
            OPTIONAL MATCH (c)-[:CASE_PENALIZED_BY]->(pen:Penalty)
            RETURN c.title AS case_title,
                   c.case_type AS case_type,
                   collect(DISTINCT art.article_no) AS articles,
                   collect(DISTINCT pen.name_zh) AS penalties
            LIMIT 10
        """,
        "extract": ["keyword"],
    },
    # 第 X 条
    r"第[一二三四五六七八九十百千零〇0-9]+条": {
        "cypher": """
            MATCH (a:Article)
            WHERE a.article_no = $article_no
            OPTIONAL MATCH (a)<-[:CONTAINS_ARTICLE]-(parent)
            RETURN a.article_no AS article_no,
                   a.content AS content,
                   a.themes AS themes,
                   parent.name AS parent_doc
            LIMIT 5
        """,
        "extract": ["article_no"],
    },
    # 处罚措施
    r"(处罚|罚|刑|拘留|罚款)": {
        "cypher": """
            MATCH (p:Penalty)
            WHERE p.name_zh CONTAINS $penalty_keyword OR p.type CONTAINS $penalty_keyword
            OPTIONAL MATCH (art:Article)-[:ARTICLE_DEFINES_PENALTY]->(p)
            RETURN p.name_zh AS penalty,
                   p.type AS type,
                   collect(DISTINCT art.article_no)[0..5] AS related_articles
            LIMIT 20
        """,
        "extract": ["penalty_keyword"],
    },
    # 违法类型
    r"(违法|违规|超标|偷排)": {
        "cypher": """
            MATCH (v:Violation)
            WHERE v.name_zh CONTAINS $violation_keyword
            OPTIONAL MATCH (art:Article)-[:ARTICLE_DEFINES_VIOLATION]->(v)
            OPTIONAL MATCH (v)<-[:CASE_INVOLVES_VIOLATION]-(c:Case)
            RETURN v.name_zh AS violation,
                   collect(DISTINCT art.article_no)[0..5] AS articles,
                   collect(DISTINCT c.title)[0..5] AS cases
            LIMIT 20
        """,
        "extract": ["violation_keyword"],
    },
}


def extract_params(query: str) -> Dict[str, str]:
    """提取 Cypher 参数"""
    params = {}
    m = re.search(r"((?:GB|HJ|DB)\s*[\d\-—]+)", query, re.IGNORECASE)
    if m:
        params["std_id"] = m.group(1).replace(" ", "")
    m = re.search(r"(SO2|NOx|PM2\.5|PM10|VOCs|COD|氨氮|NH3-N|危险废物|危废|总磷|总氮)", query, re.IGNORECASE)
    if m:
        params["pollutant"] = m.group(1)
    m = re.search(r"(钢铁|化工|印染|造纸|火电|水泥|制药|石化|焦化|有色|电镀|电池|畜禽|玻璃|陶瓷|纺织)", query)
    if m:
        params["industry"] = m.group(1)
    m = re.search(r"第([一二三四五六七八九十百千零〇0-9]+)条", query)
    if m:
        params["article_no"] = "第" + m.group(1) + "条"
    m = re.search(r"(罚款|拘留|停产|吊销|查封|责令)", query)
    if m:
        params["penalty_keyword"] = m.group(1)
    m = re.search(r"(超标|偷排|违法|违规|未批先建|未取得|无证|数据造假|伪造|篡改)", query)
    if m:
        params["violation_keyword"] = m.group(1)
    if not params:
        params["keyword"] = query[:30]
    return params


def match_template(query: str) -> Optional[Dict]:
    """匹配规则模板"""
    for pattern, rule in TEMPLATE_RULES.items():
        if re.search(pattern, query, re.IGNORECASE):
            params = extract_params(query)
            # 校验必需参数
            if all(params.get(k) for k in rule["extract"]):
                return {"cypher": rule["cypher"], "params": params}
    return None


def generate_with_glm(query: str, ontology_yaml: str) -> Optional[Dict]:
    """GLM 兜底: 自然语言 → Cypher"""
    try:
        from zai import ZhipuAiClient
    except ImportError:
        return None

    client = ZhipuAiClient(api_key="f275cb076eab46d697c1285755ab4459.U1t2diOzRAwBYEWm")
    prompt = f"""你是 Neo4j Cypher 专家。基于以下图谱 schema, 把用户问题转成 Cypher 查询。

## Schema
{ontology_yaml}

## 用户问题
{query}

## 输出格式 (严格 JSON)
```json
{{
  "cypher": "MATCH (n:Label) ... RETURN ...",
  "params": {{"param1": "value1"}}
}}
```

要求:
1. 参数化查询, 不用字符串拼接
2. 用 OPTIONAL MATCH 避免空值
3. LIMIT 20
"""
    try:
        resp = client.chat.completions.create(
            model="glm-4.7-flash",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.0,
            thinking={"type": "disabled"},
        )
        text = resp.choices[0].message.content
        m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
    except Exception as e:
        logger.warning(f"GLM text2Cypher 失败: {e}")
    return None


def text_to_cypher(query: str, use_glm: bool = True) -> Dict:
    """
    主入口: 自然语言 → Cypher
    返回: {"cypher": str, "params": dict, "method": "template|glm"}
    """
    # 1. 规则模板
    result = match_template(query)
    if result:
        result["method"] = "template"
        return result

    # 2. GLM 兜底
    if use_glm:
        ontology_path = Path(__file__).resolve().parent.parent / "ontology" / "environmental_kg.yaml"
        if ontology_path.exists():
            ontology = ontology_path.read_text(encoding="utf-8")[:3000]
            result = generate_with_glm(query, ontology)
            if result:
                result["method"] = "glm"
                return result

    # 3. 兜底: 返回 MATCH (n) RETURN n
    return {
        "cypher": "MATCH (n) RETURN n LIMIT 10",
        "params": {},
        "method": "fallback",
    }


def execute_on_networkx(cypher: str, params: Dict, graph) -> List[Dict]:
    """
    在 NetworkXGraphStore 上模拟执行
    简化实现: 解析 cypher 模式 (MATCH (n:Label {field: $val}) RETURN n.field)
    """
    # 提取 label
    label_match = re.search(r"\(n:(\w+)", cypher)
    label = label_match.group(1) if label_match else None

    # 提取返回字段
    returns = re.findall(r"n\.(\w+)\s+AS\s+(\w+)", cypher)

    # 在 graph._label_index 里查
    results = []
    nodes = graph._label_index.get(label, set()) if label else set()
    for nid in nodes:
        node = graph._nodes.get(nid)
        if not node:
            continue
        # 参数过滤
        if params:
            for k, v in params.items():
                if node["props"].get(k) and v not in str(node["props"].get(k, "")):
                    break
            else:
                pass
        else:
            pass
        rec = {"id": nid, "label": node["label"]}
        rec.update(node["props"])
        # 重命名
        for src, dst in returns:
            if src in rec:
                rec[dst] = rec[src]
        results.append(rec)
        if len(results) >= 20:
            break
    return results


if __name__ == "__main__":
    print("=" * 60)
    print("text2Cypher 测试")
    print("=" * 60)

    test_queries = [
        "GB 3095-2012 标准限制了哪些污染物?",
        "SO2 涉及哪些处罚条款?",
        "钢铁行业适用哪些标准?",
        "类似监测数据造假的案例?",
        "中华人民共和国水污染防治法第八十三条?",
        "超标排放有什么处罚?",
    ]

    for q in test_queries:
        print(f"\n>>> Q: {q}")
        result = text_to_cypher(q, use_glm=False)
        print(f"  Method: {result['method']}")
        print(f"  Cypher: {result['cypher'][:200]}")
        print(f"  Params: {result['params']}")
