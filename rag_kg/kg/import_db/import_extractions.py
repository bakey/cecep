# -*- coding: utf-8 -*-
"""把 LLM 抽取结果导入图谱"""
import sys
sys.path.insert(0, '.')
import json
from pathlib import Path
from kg.import_db.graph_store import NetworkXGraphStore, make_id, GRAPH_DIR

GRAPH = GRAPH_DIR / "graph_full.json"
extractions_path = Path(sys.argv[1] if len(sys.argv) > 1 else r"kg\logs\extractions_20.jsonl")

print(f"=== 导入 {extractions_path} 到图谱 ===")

g = NetworkXGraphStore()
if GRAPH.exists():
    g.load(str(GRAPH))
    print(f"  加载已有图: {g.stats()['nodes_total']} 节点")
else:
    from kg.import_db.graph_store import import_metadata_into_graph
    n = import_metadata_into_graph(g)
    print(f"  导入 metadata: {n} 节点")

n_nodes_before = g.stats()['nodes_total']
n_edges_before = g.stats()['edges_total']

label_id_field = {
    "Pollutant": "poll_id",
    "PollutionSource": "src_id",
    "Industry": "ind_id",
    "TreatmentTech": "tech_id",
    "Region": "region_id",
    "Organization": "org_id",
    "Case": "case_id",
    "Violation": "vio_id",
    "Penalty": "pen_id",
    "Article": "article_id",
    "Law": "law_id",
    "Regulation": "reg_id",
    "Standard": "std_id",
}

n_records = 0
n_articles = 0
n_entities = 0
n_rels = 0
for line in extractions_path.read_text(encoding='utf-8').splitlines():
    if not line.strip():
        continue
    rec = json.loads(line)
    if rec.get('error'):
        continue
    ext = rec.get('extraction', {})
    doc_id = rec.get('doc_id', '')
    if not doc_id:
        continue

    # 1. Article 节点 (从 chunk 元数据)
    art_no = rec.get('article_no')
    if art_no:
        article_id = make_id('Article', f'{doc_id}_{art_no}')
        g.add_node('Article', article_id, {
            'article_no': art_no,
            'parent_doc': doc_id[:200],
            'text_preview': rec.get('text_preview', '')[:500],
        })
        n_articles += 1
        n_records += 1

        # 把 Article 关联到父文档 (Law/Regulation/Standard)
        # 父文档 ID 用 make_id (与 metadata.json 导入保持一致)
        parent_id = make_id('Document', doc_id)
        g.add_node('Document', parent_id, {'name': doc_id[:200]})
        g.add_rel('Document', parent_id, 'CONTAINS_ARTICLE', 'Article', article_id)

    # 2. Entities
    for etype, ents in ext.get('entities', {}).items():
        id_field = label_id_field.get(etype)
        if not id_field:
            continue
        for e in ents:
            name = e.get('name') or e.get('title') or ''
            if not name:
                continue
            ent_id = make_id(etype, name)
            props = {'name_zh': name[:100]}
            for k, v in e.items():
                if k not in ('name', 'title') and v:
                    props[k] = str(v)[:200]
            g.add_node(etype, ent_id, props)
            n_entities += 1
            n_records += 1

    # 3. Relationships
    for rel in ext.get('relationships', []):
        rtype = rel.get('rel', '')
        if not rtype:
            continue
        from_type = rel.get('from_type', '')
        from_id = rel.get('from_id', '')
        from_name = rel.get('from_name', '')
        to_type = rel.get('to_type', '')
        to_name = rel.get('to_name', '')

        if not to_type or not to_name:
            continue

        # 解析 from
        if from_type == 'Article':
            art_no_from = from_id
            if art_no_from:
                fid = make_id('Article', f'{doc_id}_{art_no_from}')
                flabel = 'Article'
            else:
                continue
        elif from_type and from_name:
            fid = make_id(from_type, from_name)
            flabel = from_type
        else:
            continue

        tid = make_id(to_type, to_name)
        tlabel = to_type

        g.add_rel(flabel, fid, rtype, tlabel, tid, rel.get('props', {}))
        n_rels += 1

stats = g.stats()
print(f"\n=== 增量统计 ===")
print(f"  新增 Article 节点: {n_articles}")
print(f"  新增实体节点: {n_entities}")
print(f"  新增关系: {n_rels}")
print(f"  节点总数: {stats['nodes_total']} (增 {stats['nodes_total'] - n_nodes_before})")
print(f"  关系总数: {stats['edges_total']} (增 {stats['edges_total'] - n_edges_before})")
print(f"  节点类型: {stats['by_label']}")
print(f"  关系类型: {stats['by_rel']}")

g.persist(str(GRAPH))
print(f"\n  持久化: {GRAPH}")
