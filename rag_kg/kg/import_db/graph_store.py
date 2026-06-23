# -*- coding: utf-8 -*-
"""
GraphStore 抽象层
  - GraphStoreProtocol: 接口契约
  - NetworkXGraphStore: 内存实现 (骨架演示)
  - Neo4jGraphStore: 真实生产 (骨架就绪后切换)

设计原则:
  - 所有调用走同一个 GraphStoreProtocol
  - 切换 backend 只需改一行
"""
import os
import json
import re
import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from abc import ABC, abstractmethod
from collections import defaultdict

logger = logging.getLogger("kg.graph")

KG_DIR = Path(__file__).resolve().parent.parent
GRAPH_DIR = KG_DIR / "import_db" / "graph_store"
GRAPH_DIR.mkdir(parents=True, exist_ok=True)


# -----------------------------------------------------------------------------
# 实体 ID 生成
# -----------------------------------------------------------------------------
def make_id(etype: str, *parts) -> str:
    raw = f"{etype}::" + "::".join(str(p) for p in parts if p)
    h = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^\w\u4e00-\u9fa5]", "_", parts[0])[:30] if parts else etype
    return f"{etype}_{safe}_{h}"


# -----------------------------------------------------------------------------
# 接口
# -----------------------------------------------------------------------------
class GraphStoreProtocol(ABC):
    @abstractmethod
    def add_node(self, label: str, node_id: str, props: Dict): ...
    @abstractmethod
    def add_rel(self, from_label: str, from_id: str, rel: str, to_label: str, to_id: str, props: Dict = None): ...
    @abstractmethod
    def query(self, cypher: str, params: Dict = None) -> List[Dict]: ...
    @abstractmethod
    def text_search(self, query: str, top_k: int = 10) -> List[Dict]: ...
    @abstractmethod
    def stats(self) -> Dict: ...
    @abstractmethod
    def persist(self, path: str): ...
    @abstractmethod
    def load(self, path: str): ...


# -----------------------------------------------------------------------------
# NetworkX 实现
# -----------------------------------------------------------------------------
class NetworkXGraphStore(GraphStoreProtocol):
    """
    内存图存储, 支持:
      - 多标签节点 (label 作为节点属性)
      - 多类型关系 (type+props 作为边属性)
      - 简单 Cypher-like 查询 (WHERE 字段=值 AND ... RETURN 节点/关系)
      - 全文搜索 (节点 name_zh/full_name 包含)
    """

    def __init__(self):
        self._nodes: Dict[str, Dict] = {}      # id -> {label, props}
        self._edges: List[Dict] = []           # [{from, to, rel, props}]
        self._adjacency: Dict[str, List[Dict]] = defaultdict(list)
        self._label_index: Dict[str, set] = defaultdict(set)
        logger.info("NetworkXGraphStore 初始化 (内存)")

    def add_node(self, label: str, node_id: str, props: Dict):
        if node_id in self._nodes:
            self._nodes[node_id]["props"].update(props)
            return
        self._nodes[node_id] = {"label": label, "props": dict(props)}
        self._label_index[label].add(node_id)

    def add_rel(self, from_label: str, from_id: str, rel: str, to_label: str, to_id: str, props: Dict = None):
        if from_id not in self._nodes or to_id not in self._nodes:
            logger.debug(f"跳过关系 (节点不存在): {from_id} -[{rel}]-> {to_id}")
            return
        edge = {
            "from_id": from_id,
            "from_label": from_label,
            "rel": rel,
            "to_id": to_id,
            "to_label": to_label,
            "props": props or {},
        }
        self._edges.append(edge)
        self._adjacency[from_id].append(edge)

    def query(self, cypher: str, params: Dict = None) -> List[Dict]:
        """
        简化 Cypher 子集:
          MATCH (n:Label {key: $val}) -[r:REL]-> (m)
          WHERE n.field = $x
          RETURN n, m
        实际实现: 基于 params 做字段匹配
        """
        params = params or {}
        results = []

        # 简化: 按 from_label 找起点
        from_label = params.get("from_label")
        rel_type = params.get("rel")
        to_label = params.get("to_label")
        field_filter = params.get("field_filter", {})  # {field: value}

        candidates = (
            self._label_index.get(from_label, set()) if from_label
            else list(self._nodes.keys())
        )
        for nid in candidates:
            node = self._nodes.get(nid)
            if not node:
                continue
            if node["label"] != from_label:
                continue
            if field_filter:
                if not all(node["props"].get(k) == v for k, v in field_filter.items()):
                    continue
            for edge in self._adjacency.get(nid, []):
                if rel_type and edge["rel"] != rel_type:
                    continue
                if to_label and edge["to_label"] != to_label:
                    continue
                t_node = self._nodes.get(edge["to_id"])
                if t_node:
                    results.append({
                        "n": {"id": nid, "label": node["label"], **node["props"]},
                        "r": {"rel": edge["rel"], **edge["props"]},
                        "m": {"id": edge["to_id"], "label": t_node["label"], **t_node["props"]},
                    })
                if len(results) >= params.get("limit", 50):
                    return results
        return results

    def text_search(self, query: str, top_k: int = 10) -> List[Dict]:
        """
        节点 name_zh/full_name 包含查询关键词
        支持:
          - 精确包含 (query in field)
          - 双向包含 (field in query) - 处理 "河北" 匹配 "河北省"
          - 关键词分词: 拆 query, 任一关键词命中即得分
        """
        # 简单中文分词: 按非中文字符拆 + 单字
        import re
        keywords = [k for k in re.split(r"[\s,，。；;、]+", query) if len(k) >= 2]
        if not keywords:
            keywords = [query]
        # 也加入单字 (兜底)
        keywords += [c for c in query if '\u4e00' <= c <= '\u9fff']

        results = []
        for nid, node in self._nodes.items():
            props = node["props"]
            score = 0
            matched = False
            for field in ("name_zh", "full_name", "summary", "title"):
                v = props.get(field, "")
                if not isinstance(v, str) or not v:
                    continue
                for kw in keywords:
                    if kw in v:
                        # 长度归一化, 越短 keyword 命中权重越高
                        score += len(kw) / max(len(v), 1) * (1 + 0.1 * len(kw))
                        matched = True
                    elif v in query and len(v) >= 2:
                        # 字段值被 query 完全包含 (例如 "河北" 在 "河北省的地方标准" 里)
                        score += 0.5 * len(v) / max(len(query), 1)
                        matched = True
                if matched:
                    break
            if score > 0:
                results.append({
                    "node": {"id": nid, "label": node["label"], **props},
                    "score": score,
                })
        results.sort(key=lambda x: -x["score"])
        return results[:top_k]

    def stats(self) -> Dict:
        labels = defaultdict(int)
        rels = defaultdict(int)
        for nid, node in self._nodes.items():
            labels[node["label"]] += 1
        for e in self._edges:
            rels[e["rel"]] += 1
        return {
            "nodes_total": len(self._nodes),
            "edges_total": len(self._edges),
            "by_label": dict(labels),
            "by_rel": dict(rels),
        }

    def persist(self, path: str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "nodes": self._nodes,
            "edges": self._edges,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        logger.info(f"图谱持久化: {path} ({len(self._nodes)} 节点)")

    def load(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._nodes = data["nodes"]
        self._edges = data["edges"]
        for nid, node in self._nodes.items():
            self._label_index[node["label"]].add(nid)
        for e in self._edges:
            self._adjacency[e["from_id"]].append(e)
        logger.info(f"图谱加载: {path} ({len(self._nodes)} 节点)")


# -----------------------------------------------------------------------------
# 工厂
# -----------------------------------------------------------------------------
def get_graph_store() -> GraphStoreProtocol:
    """默认 NetworkX, 后续可改成 Neo4j"""
    backend = os.environ.get("GRAPH_BACKEND", "networkx")
    if backend == "neo4j":
        from kg.import_db.neo4j_graph_store import Neo4jGraphStore
        return Neo4jGraphStore()
    return NetworkXGraphStore()


# -----------------------------------------------------------------------------
# 从 metadata.json 导入
# -----------------------------------------------------------------------------
def import_metadata_into_graph(graph: GraphStoreProtocol, data_root: str = None) -> int:
    """Phase A: 把 3384 份 metadata.json 导入图谱"""
    import os
    if data_root is None:
        env_p = os.environ.get("ZJN_DATA_ROOT")
        if env_p:
            data_root = env_p
        else:
            cand = Path(__file__).resolve().parent.parent.parent.parent / "DataReal"
            data_root = str(cand if cand.exists() else Path(__file__).resolve().parent.parent / "DataReal")
    data_root = Path(data_root)
    n_records = 0
    for meta_path in data_root.rglob("metadata.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        ca = meta.get("compliance_assessment", {})
        if not ca:
            continue
        full_name = (ca.get("full_name") or ca.get("target_file", "")).strip()
        std_id = (ca.get("standard_id") or "").strip()
        if not full_name:
            continue

        # 判断节点类型
        is_standard = bool(std_id) or any(full_name.startswith(p) for p in ["GB ", "HJ ", "DB "])
        if is_standard:
            node_type = "Standard"
            node_id = std_id or full_name
            node_id = f"std_{re.sub(r'[^\\w]', '_', node_id)[:50]}"
            props = {
                "std_id": std_id,
                "full_name": full_name,
                "std_type": ca.get("standard_type", ""),
                "validity_status": "现行" if ca.get("validity_status") is True else "未知",
                "effective_date": ca.get("effective_date", ""),
            }
        elif "条例" in full_name or "法" == full_name[-1] or "办法" in full_name:
            node_type = "Regulation" if ca.get("region_type") == "地方" else "Law"
            node_id = make_id(node_type, full_name)
            props = {
                "full_name": full_name,
                "level": "地方" if ca.get("region_type") == "地方" else "国家",
                "region": ca.get("region_name", ""),
                "effective_date": ca.get("effective_date", ""),
                "validity_status": "现行" if ca.get("validity_status") is True else "未知",
                "summary": (ca.get("summary", "") or "")[:500],
            }
        else:
            continue

        graph.add_node(node_type, node_id, props)
        n_records += 1

        # Organization
        for org in ca.get("issuing_authority", []):
            org = (org or "").strip()
            if not org:
                continue
            org_id = make_id("Organization", org)
            graph.add_node("Organization", org_id, {
                "name_zh": org,
                "level": "国家" if any(k in org for k in ["中华人民共和国", "生态环境部", "国家", "全国"]) else "地方",
            })
            graph.add_rel(node_type, node_id, "ISSUED_BY", "Organization", org_id)

        # Region
        region = (ca.get("region_name") or "").strip()
        if region:
            region_id = make_id("Region", region)
            graph.add_node("Region", region_id, {
                "name_zh": region,
                "level": ca.get("region_type", ""),
            })
            graph.add_rel(node_type, node_id, "APPLIES_TO_REGION", "Region", region_id)

        # Supersedes
        for sup in ca.get("supersedes", []):
            sup_name = (sup.get("name", "") or "").strip()
            if not sup_name:
                continue
            sup_id = make_id("Document", sup_name)
            graph.add_node("Document", sup_id, {"name": sup_name})
            graph.add_rel(node_type, node_id, "SUPERSEDES", "Document", sup_id, {
                "effective_date": sup.get("supersedes_time", ""),
            })

    return n_records


if __name__ == "__main__":
    import sys
    g = NetworkXGraphStore()
    print("=== 导入 metadata.json ===")
    n = import_metadata_into_graph(g)
    print(f"导入 {n} 节点")
    stats = g.stats()
    print(f"图谱统计: {json.dumps(stats, ensure_ascii=False, indent=2)}")
    g.persist(GRAPH_DIR / "graph_metadata.json")
