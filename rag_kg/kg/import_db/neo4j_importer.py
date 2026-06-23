# -*- coding: utf-8 -*-
"""
Neo4j 知识图谱入库器
特性:
  - 读取 extractions.jsonl, 同时结合 metadata.json 结构化字段
  - 使用 apoc.merge.node / apoc.merge.relationship 实现 MERGE 增量
  - 实体去重 (按 name_zh + category)
  - 干跑模式 (--dry-run) 只生成 Cypher 不执行
  - 索引/约束自动创建
"""
import os
import re
import json
import argparse
import logging
from pathlib import Path
from typing import Dict, List, Optional
from collections import defaultdict

try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ServiceUnavailable
except ImportError:
    GraphDatabase = None
    print("请先安装: pip install neo4j")

# -----------------------------------------------------------------------------
# 配置
# -----------------------------------------------------------------------------
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "neo4j1234")

DATA_REAL = Path(__file__).resolve().parent.parent.parent / "DataReal"
KG_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = KG_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("kg.importer")
logger.setLevel(logging.INFO)
fh = logging.FileHandler(LOG_DIR / "import.log", encoding="utf-8")
fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(fh)


# -----------------------------------------------------------------------------
# 实体 ID 构造 (确保同名实体有同一 ID)
# -----------------------------------------------------------------------------
def make_entity_id(etype: str, name: str, extra: str = "") -> str:
    raw = f"{etype}::{name}::{extra}".strip("::")
    h = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^\w\u4e00-\u9fa5]", "_", name)[:30]
    return f"{etype}_{safe}_{h}"


import hashlib

# -----------------------------------------------------------------------------
# Cypher 模板
# -----------------------------------------------------------------------------
CONSTRAINTS = [
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Law) REQUIRE n.law_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Regulation) REQUIRE n.reg_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Standard) REQUIRE n.std_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Article) REQUIRE n.article_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Pollutant) REQUIRE n.poll_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:PollutionSource) REQUIRE n.src_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Industry) REQUIRE n.ind_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:TreatmentTech) REQUIRE n.tech_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Region) REQUIRE n.region_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Organization) REQUIRE n.org_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Case) REQUIRE n.case_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Violation) REQUIRE n.vio_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Penalty) REQUIRE n.pen_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Enterprise) REQUIRE n.ent_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Interpretation) REQUIRE n.interp_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Notice) REQUIRE n.notice_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Chunk) REQUIRE n.chunk_id IS UNIQUE",
    "CREATE INDEX IF NOT EXISTS FOR (n:Pollutant) ON (n.name_zh)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Industry) ON (n.name_zh)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Region) ON (n.name_zh)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Article) ON (n.parent_doc)",
]

# -----------------------------------------------------------------------------
# Phase A: 录入 metadata.json 静态结构 (Law/Regulation/Standard/Region/Organization/...)
# -----------------------------------------------------------------------------
def parse_metadata_for_graph() -> List[Dict]:
    """遍历 DataReal/metadata.json, 提取静态层实体和关系"""
    records = []
    for meta_path in DATA_REAL.rglob("metadata.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        ca = meta.get("compliance_assessment", {})
        if not ca:
            continue

        full_name = ca.get("full_name") or ca.get("target_file", "")
        std_id = ca.get("standard_id", "").strip() or None
        if not full_name:
            continue

        # 判断节点类型
        is_standard = bool(std_id) or any(
            full_name.startswith(p) for p in ["GB ", "HJ ", "DB "]
        )
        if is_standard:
            node_type = "Standard"
            node_id = std_id or full_name
            node_id = f"std_{re.sub(r'[^\\w]', '_', node_id)[:50]}"
            props = {
                "std_id": std_id or "",
                "full_name": full_name,
                "std_type": ca.get("standard_type") or (
                    "国家标准" if (std_id and std_id.startswith("GB"))
                    else "行业标准" if (std_id and std_id.startswith("HJ"))
                    else "地方标准" if (std_id and std_id.startswith("DB"))
                    else ""
                ),
                "validity_status": str(ca.get("validity_status", "")),
                "status": "现行" if ca.get("validity_status") is True else "未知",
            }
        elif "条例" in full_name or "法" == full_name[-1] or "办法" in full_name:
            node_type = "Regulation" if ca.get("region_type") == "地方" else "Law"
            node_id = make_entity_id(node_type, full_name)
            props = {
                "full_name": full_name,
                "level": "地方" if ca.get("region_type") == "地方" else "国家",
                "region": ca.get("region_name", ""),
                "effective_date": ca.get("effective_date", ""),
                "status": "现行" if ca.get("validity_status") is True else "未知",
                "summary": ca.get("summary", ""),
            }
        else:
            # 案例/解读/通知 暂跳过, 由 LLM 抽取阶段处理
            continue

        records.append({
            "type": "NODE",
            "label": node_type,
            "id_field": "law_id" if node_type == "Law" else "reg_id" if node_type == "Regulation" else "std_id",
            "id": node_id,
            "props": props,
            "source_path": str(meta_path.parent),
        })

        # Organization
        for org in ca.get("issuing_authority", []):
            org = org.strip()
            if org:
                records.append({
                    "type": "NODE",
                    "label": "Organization",
                    "id_field": "org_id",
                    "id": make_entity_id("Organization", org),
                    "props": {
                        "name_zh": org,
                        "level": "国家" if any(k in org for k in ["中华人民共和国", "生态环境部", "国家", "全国"]) else "地方",
                    },
                })
                records.append({
                    "type": "REL",
                    "from_label": node_type,
                    "from_id": node_id,
                    "rel": "ISSUED_BY",
                    "to_label": "Organization",
                    "to_id": make_entity_id("Organization", org),
                })

        # Region
        region = ca.get("region_name", "").strip()
        if region:
            records.append({
                "type": "NODE",
                "label": "Region",
                "id_field": "region_id",
                "id": make_entity_id("Region", region),
                "props": {
                    "name_zh": region,
                    "level": ca.get("region_type", ""),
                },
            })
            records.append({
                "type": "REL",
                "from_label": node_type,
                "from_id": node_id,
                "rel": "APPLIES_TO_REGION",
                "to_label": "Region",
                "to_id": make_entity_id("Region", region),
            })

        # Supersedes
        for sup in ca.get("supersedes", []):
            sup_name = sup.get("name", "").strip()
            if not sup_name:
                continue
            sup_id = make_entity_id("Document", sup_name)
            records.append({
                "type": "REL",
                "from_label": node_type,
                "from_id": node_id,
                "rel": "SUPERSEDES",
                "to_label": "Document",
                "to_id": sup_id,
                "props": {"effective_date": sup.get("supersedes_time", "")},
            })

    return records


# -----------------------------------------------------------------------------
# Phase B: 录入 LLM 抽取结果
# -----------------------------------------------------------------------------
def parse_extractions_for_graph(extractions_path: Path) -> List[Dict]:
    records = []
    if not extractions_path.exists():
        return records

    for line in extractions_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue

        if rec.get("error"):
            continue

        ext = rec.get("extraction", {})
        doc_id = rec.get("doc_id", "")
        if not doc_id:
            continue

        # Articles
        article_id_map = {}
        for i, art in enumerate(ext.get("articles", [])):
            art_no = art.get("article_no", "").strip()
            if not art_no:
                continue
            article_id = make_entity_id("Article", f"{doc_id}_{art_no}")
            article_id_map[art_no] = article_id
            records.append({
                "type": "NODE",
                "label": "Article",
                "id_field": "article_id",
                "id": article_id,
                "props": {
                    "article_no": art_no,
                    "content": art.get("content", "")[:5000],
                    "content_hash": hashlib.md5(art.get("content", "").encode("utf-8")).hexdigest(),
                    "themes": art.get("themes", []),
                    "obligations": art.get("obligations", []),
                    "penalty": art.get("penalty_text", "")[:1000],
                    "parent_doc": doc_id,
                },
            })
            # Article 属于父文档 (Law/Regulation/Standard)
            # 父文档 ID 用 make_entity_id
            parent_id = None
            parent_label = None
            ca = rec.get("doc_meta", {}).get("compliance_assessment", {})
            if ca.get("standard_id"):
                parent_id = f"std_{re.sub(r'[^\\w]', '_', ca['standard_id'])[:50]}"
                parent_label = "Standard"
            elif ca.get("full_name"):
                full = ca["full_name"]
                if "条例" in full or "法" == full[-1] or "办法" in full:
                    parent_label = "Regulation" if ca.get("region_type") == "地方" else "Law"
                else:
                    parent_label = "Standard"
                parent_id = make_entity_id(parent_label, full)
            if parent_id and parent_label:
                records.append({
                    "type": "REL",
                    "from_label": parent_label,
                    "from_id": parent_id,
                    "rel": "CONTAINS_ARTICLE",
                    "to_label": "Article",
                    "to_id": article_id,
                })

        # Entities
        for etype, ents in ext.get("entities", {}).items():
            label = etype  # Pollutant, Industry, ...
            id_field_map = {
                "Pollutant": "poll_id",
                "PollutionSource": "src_id",
                "Industry": "ind_id",
                "TreatmentTech": "tech_id",
                "Region": "region_id",
                "Organization": "org_id",
                "Case": "case_id",
                "Violation": "vio_id",
                "Penalty": "pen_id",
            }
            id_field = id_field_map.get(label)
            if not id_field:
                continue
            for e in ents:
                name = e.get("name") or e.get("title") or ""
                if not name:
                    continue
                ent_id = make_entity_id(label, name)
                props = {"name_zh": name}
                for k, v in e.items():
                    if k not in ("name", "title") and v:
                        props[k] = v
                records.append({
                    "type": "NODE",
                    "label": label,
                    "id_field": id_field,
                    "id": ent_id,
                    "props": props,
                })

        # Relationships
        for rel in ext.get("relationships", []):
            from_type = rel.get("from_type", "")
            from_id = rel.get("from_id", "")
            from_name = rel.get("from_name", "")
            to_type = rel.get("to_type", "")
            to_name = rel.get("to_name", "")
            rtype = rel.get("rel", "")
            if not rtype or not to_type or not to_name:
                continue

            if from_type == "Article":
                fid = article_id_map.get(from_id, "")
                flabel = "Article"
            else:
                fid = make_entity_id(from_type, from_name) if from_name else ""
                flabel = from_type

            tid = make_entity_id(to_type, to_name)
            tlabel = to_type

            if not fid or not tid:
                continue

            records.append({
                "type": "REL",
                "from_label": flabel,
                "from_id": fid,
                "rel": rtype,
                "to_label": tlabel,
                "to_id": tid,
                "props": rel.get("props", {}),
            })

    return records


# -----------------------------------------------------------------------------
# Cypher 生成 & 执行
# -----------------------------------------------------------------------------
def make_node_cypher(rec: Dict) -> str:
    label = rec["label"]
    id_field = rec["id_field"]
    nid = rec["id"].replace("'", "\\'")
    props = rec["props"]
    props["source"] = rec.get("source_path", "extraction")

    # 序列化 props
    props_str = ", ".join(
        f"{k}: {json.dumps(v, ensure_ascii=False)}"
        for k, v in props.items()
        if v is not None and v != ""
    )
    props_str += f", {id_field}: {json.dumps(nid)}"

    return f"MERGE (n:{label} {{{id_field}: {json.dumps(nid)}}}) SET n += {{{props_str}}}"


def make_rel_cypher(rec: Dict) -> str:
    fl = rec["from_label"]
    fid = rec["from_id"].replace("'", "\\'")
    tl = rec["to_label"]
    tid = rec["to_id"].replace("'", "\\'")
    rt = rec["rel"]
    props = rec.get("props", {})
    props_str = ""
    if props:
        props_str = " {" + ", ".join(
            f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in props.items()
        ) + "}"
    # 简化: 用 id 匹配 (假设节点唯一字段就是 id_field)
    return (
        f"MATCH (a:{fl}), (b:{tl}) "
        f"WHERE a.{_id_field(fl)} = {json.dumps(fid)} AND b.{_id_field(tl)} = {json.dumps(tid)} "
        f"MERGE (a)-[r:{rt}]->(b) "
        f"SET r += {json.dumps(props, ensure_ascii=False) if props else '{}'}"
    )


def _id_field(label: str) -> str:
    return {
        "Law": "law_id", "Regulation": "reg_id", "Standard": "std_id",
        "Article": "article_id", "Pollutant": "poll_id", "PollutionSource": "src_id",
        "Industry": "ind_id", "TreatmentTech": "tech_id", "Region": "region_id",
        "Organization": "org_id", "Case": "case_id", "Violation": "vio_id",
        "Penalty": "pen_id", "Enterprise": "ent_id", "Interpretation": "interp_id",
        "Notice": "notice_id", "Chunk": "chunk_id", "Document": "name",
    }.get(label, "id")


def import_records(records: List[Dict], dry_run: bool = False, batch_size: int = 200):
    if not records:
        print("无数据")
        return

    if dry_run:
        cypher_path = LOG_DIR / "import_dryrun.cypher"
        with open(cypher_path, "w", encoding="utf-8") as f:
            f.write("// Generated dry-run Cypher\n")
            for c in CONSTRAINTS:
                f.write(c + ";\n")
            for rec in records:
                f.write((make_node_cypher(rec) if rec["type"] == "NODE" else make_rel_cypher(rec)) + ";\n")
        print(f"干跑 Cypher 已写入 {cypher_path}, 共 {len(records)} 条")
        return

    if GraphDatabase is None:
        print("请安装 neo4j python driver")
        return

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:
        # 创建约束
        for c in CONSTRAINTS:
            try:
                session.run(c)
            except Exception as e:
                logger.warning(f"约束创建失败 (可能已存在): {e}")

        # 分批导入
        node_count = 0
        rel_count = 0
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            tx = session.begin_transaction()
            try:
                for rec in batch:
                    if rec["type"] == "NODE":
                        tx.run(make_node_cypher(rec))
                        node_count += 1
                    else:
                        tx.run(make_rel_cypher(rec))
                        rel_count += 1
                tx.commit()
                logger.info(f"已导入 {min(i + batch_size, len(records))} / {len(records)}")
            except Exception as e:
                tx.rollback()
                logger.error(f"批次 {i} 失败: {e}")
                continue

    driver.close()
    print(f"导入完成: 节点 {node_count}, 关系 {rel_count}")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["metadata", "extraction", "all"], default="all")
    parser.add_argument("--extractions", default=str(LOG_DIR / "extractions.jsonl"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    all_records = []
    if args.phase in ("metadata", "all"):
        logger.info("=== Phase A: metadata.json ===")
        all_records.extend(parse_metadata_for_graph())
    if args.phase in ("extraction", "all"):
        logger.info("=== Phase B: LLM extractions ===")
        all_records.extend(parse_extractions_for_graph(Path(args.extractions)))

    # 去重 (按 type+id+label)
    seen = set()
    deduped = []
    for r in all_records:
        key = (r["type"], r.get("label", r.get("from_label", "")), r.get("id", r.get("from_id", "")))
        if r["type"] == "REL":
            key = (r["type"], r["from_label"], r["from_id"], r["rel"], r["to_label"], r["to_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    logger.info(f"去重前 {len(all_records)}, 去重后 {len(deduped)}")
    import_records(deduped, dry_run=args.dry_run)
