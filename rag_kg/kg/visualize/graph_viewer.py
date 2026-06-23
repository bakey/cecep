# -*- coding: utf-8 -*-
"""
图谱可视化 (pyvis)
  - 加载 NetworkXGraphStore
  - 输入关键词 → 渲染子图 (节点 + 关系)
  - 输出 HTML 文件
"""
import sys
from pathlib import Path
from collections import defaultdict

KG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KG_DIR.parent))

OUT_DIR = KG_DIR / "visualize" / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

LABEL_COLORS = {
    "Law": "#FF6B6B",
    "Regulation": "#FF6B6B",
    "Standard": "#4ECDC4",
    "Article": "#95E1D3",
    "Pollutant": "#FFE66D",
    "PollutionSource": "#FFA07A",
    "Industry": "#A8E6CF",
    "TreatmentTech": "#C7CEEA",
    "Violation": "#FF8B94",
    "Penalty": "#FEC8D8",
    "Case": "#957DAD",
    "Organization": "#B5EAD7",
    "Region": "#FFDAC1",
    "Document": "#E0E0E0",
}


def render_subgraph(graph, center_keyword: str, depth: int = 2, output_path: str = None):
    """
    渲染以关键词为中心的子图
    depth=1: 中心 + 直接邻居
    depth=2: 中心 + 二跳邻居
    """
    try:
        from pyvis.network import Network
    except ImportError:
        print("请先安装: pip install pyvis")
        return None

    # 找中心节点 (在所有 label 里搜)
    center_id = None
    center_node = None
    for nid, node in graph._nodes.items():
        props = node["props"]
        for field in ("name_zh", "full_name", "summary", "title"):
            v = props.get(field, "")
            if isinstance(v, str) and center_keyword in v:
                center_id = nid
                center_node = node
                break
        if center_id:
            break

    if not center_id:
        print(f"未找到关键词 '{center_keyword}' 对应节点")
        # 用 graph.text_search 兜底
        hits = graph.text_search(center_keyword, top_k=5)
        if hits:
            print(f"  提示: text_search 找到 {len(hits)} 个相关节点:")
            for h in hits[:3]:
                print(f"    - {h['node'].get('label')}: {h['node'].get('name_zh') or h['node'].get('full_name')}")
        return None

    # BFS 找子图节点
    visited = {center_id}
    queue = [(center_id, 0)]
    edges_in_subgraph = []

    while queue:
        cur_id, cur_depth = queue.pop(0)
        if cur_depth >= depth:
            continue
        for edge in graph._adjacency.get(cur_id, []):
            edges_in_subgraph.append(edge)
            for nid in [edge["from_id"], edge["to_id"]]:
                if nid not in visited:
                    visited.add(nid)
                    queue.append((nid, cur_depth + 1))

    # 建 NetworkX 图
    net = Network(
        height="800px",
        width="100%",
        directed=True,
        notebook=False,
        cdn_resources="remote",
        bgcolor="#FFFFFF",
        font_color="#222",
    )

    # 物理引擎配置
    net.set_options("""
    var options = {
      "physics": {
        "enabled": true,
        "solver": "forceAtlas2Based",
        "forceAtlas2Based": {
          "gravitationalConstant": -50,
          "centralGravity": 0.01,
          "springLength": 150,
          "springConstant": 0.08
        }
      },
      "nodes": {
        "shape": "dot",
        "size": 25,
        "font": {"size": 14}
      },
      "edges": {
        "arrows": {"to": {"enabled": true}},
        "smooth": {"type": "curvedCW"},
        "font": {"size": 11, "align": "middle"}
      }
    }
    """)

    # 加节点
    for nid in visited:
        node = graph._nodes.get(nid)
        if not node:
            continue
        props = node["props"]
        label = props.get("name_zh") or props.get("full_name") or props.get("name", nid[:20])
        color = LABEL_COLORS.get(node["label"], "#888888")
        title = f"<b>{node['label']}</b><br>{label}<br>" + "<br>".join(
            f"{k}: {v}" for k, v in list(props.items())[:5]
        )
        # 中心节点加大
        size = 35 if nid == center_id else 20
        net.add_node(nid, label=label[:50], title=title, color=color, size=size)

    # 加边
    for edge in edges_in_subgraph:
        if edge["from_id"] in visited and edge["to_id"] in visited:
            rel = edge["rel"]
            label = rel
            title = rel + " | " + ", ".join(f"{k}={v}" for k, v in edge["props"].items())
            net.add_edge(edge["from_id"], edge["to_id"], label=label[:20], title=title)

    # 保存
    if output_path is None:
        safe_kw = center_keyword.replace("/", "_").replace("\\", "_")[:30]
        output_path = OUT_DIR / f"subgraph_{safe_kw}.html"
    net.save_graph(str(output_path))

    n_nodes = len(visited)
    n_edges = len(edges_in_subgraph)
    print(f"✅ 子图已渲染: {output_path}")
    print(f"   中心: {center_node['label']} - {center_node['props'].get('name_zh') or center_node['props'].get('full_name')}")
    print(f"   节点: {n_nodes}, 边: {n_edges}, 深度: {depth}")

    return str(output_path)


def render_full_overview(graph, max_nodes: int = 200, output_path: str = None):
    """渲染图谱概览 (限制节点数避免卡顿)"""
    try:
        from pyvis.network import Network
    except ImportError:
        print("请先安装: pip install pyvis")
        return None

    net = Network(height="900px", width="100%", directed=True, notebook=False, cdn_resources="remote")
    net.set_options("""
    var options = {
      "physics": {
        "enabled": true,
        "solver": "forceAtlas2Based",
        "forceAtlas2Based": {
          "gravitationalConstant": -80,
          "centralGravity": 0.005
        }
      },
      "nodes": {"shape": "dot", "size": 12, "font": {"size": 11}}
    }
    """)

    # 按 label 采样
    sampled = []
    label_counts = defaultdict(int)
    for nid, node in graph._nodes.items():
        if label_counts[node["label"]] < max_nodes // 8:
            sampled.append(nid)
            label_counts[node["label"]] += 1
        if len(sampled) >= max_nodes:
            break
    sampled_set = set(sampled)

    # 加节点
    for nid in sampled:
        node = graph._nodes[nid]
        props = node["props"]
        label = props.get("name_zh") or props.get("full_name") or nid[:15]
        color = LABEL_COLORS.get(node["label"], "#888888")
        net.add_node(nid, label=label[:30], title=node["label"], color=color, size=10)

    # 加边 (只保留两端都在 sampled 里的)
    edges_added = 0
    for edge in graph._edges:
        if edge["from_id"] in sampled_set and edge["to_id"] in sampled_set:
            net.add_edge(edge["from_id"], edge["to_id"], label=edge["rel"][:15])
            edges_added += 1
            if edges_added > max_nodes * 3:
                break

    if output_path is None:
        output_path = OUT_DIR / "graph_overview.html"
    net.save_graph(str(output_path))

    stats = graph.stats()
    print(f"✅ 概览已渲染: {output_path}")
    print(f"   显示: {len(sampled)} / {stats['nodes_total']} 节点, {edges_added} 边")
    print(f"   节点类型: {stats['by_label']}")
    return str(output_path)


if __name__ == "__main__":
    from kg.import_db.graph_store import NetworkXGraphStore

    print("加载图谱...")
    g = NetworkXGraphStore()
    cache = KG_DIR / "import_db" / "graph_store" / "graph_full.json"
    if cache.exists():
        g.load(str(cache))
    stats = g.stats()
    print(f"  {stats['nodes_total']} 节点 / {stats['edges_total']} 关系")

    print("\n[1] 概览图")
    render_full_overview(g, max_nodes=200)

    print("\n[2] SO2 子图 (depth=2)")
    render_subgraph(g, "SO2", depth=2)

    print("\n[3] 钢铁 子图 (depth=2)")
    render_subgraph(g, "钢铁", depth=2)

    print("\n[4] 危险废物 子图 (depth=2)")
    render_subgraph(g, "危险废物", depth=2)

    print("\n[5] 水污染防治法 子图 (depth=1)")
    render_subgraph(g, "水污染防治法", depth=1)
