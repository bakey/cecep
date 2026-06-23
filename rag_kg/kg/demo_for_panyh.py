"""
中节能 RAG+KG 骨架 v1.0 - 面向潘云泓交付演示

运行: python kg/demo_for_panyh.py

特性:
  - 5 智能体一键演示 (qa/nl2sql/tracing/compliance/fraud)
  - 自动打印证据 + 引用 + 召回节点
  - 10 道精选业务问题, 覆盖 5 类意图
"""
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kg.skeleton_v1 import (
    load_graph, load_vector, load_bm25, load_embedder,
    HybridRetriever, RAGChain, call_skill
)

KG_DIR = Path(__file__).resolve().parent


def banner(title):
    print()
    print("=" * 78)
    print(f"  {title}")
    print("=" * 78)


def show_result(skill, q, result, t):
    print(f"\n[{skill}] {q}")
    print(f"耗时: {t:.1f}s")

    if "error" in result and "answer" not in result:
        print(f"\n[错误] {result['error']}")
        return

    if "answer" in result:
        ans = result['answer'] or ""
        if "GLM 错误" in ans or "限流" in ans or "生成失败" in ans:
            print(f"\n[回答] [API 限流, 仅展示检索证据]")
        else:
            print(f"\n[回答]\n{ans[:500]}")

    if "citations" in result and result['citations']:
        print(f"\n[引用] {result['citations'][:3]}")
    if "evidence_count" in result:
        print(f"[证据数] {result['evidence_count']}")

    ev = result.get("evidence") or result.get("hits") or result.get("results") or []
    if ev:
        print(f"\n[证据] {len(ev)} 条:")
        for i, e in enumerate(ev[:5], 1):
            if isinstance(e, dict):
                label = e.get("label", e.get("type", "?"))
                text = (e.get("text") or e.get("name_zh") or e.get("title", ""))[:80]
                src = e.get("source", "?")
                print(f"  {i}. [{src}|{label}] {text}")
            else:
                print(f"  {i}. {str(e)[:80]}")

    if "data" in result:
        print(f"\n[结构化结果]")
        if isinstance(result['data'], (list, dict)):
            print(json.dumps(result['data'], ensure_ascii=False, indent=2)[:500])
        else:
            print(result['data'][:500])

    if "sub_results" in result:
        sub = result['sub_results']
        if sub:
            print(f"\n[子结果]")
            for k, v in list(sub.items())[:3]:
                if isinstance(v, list) and v:
                    print(f"  {k}: {len(v)} 项 (例: {v[0] if v else 'N/A'})")
                else:
                    print(f"  {k}: {v}")

    for k in ("rows", "pollutants", "sources", "applicable_standards"):
        if k in result and result[k]:
            v = result[k]
            if isinstance(v, list):
                print(f"\n[{k}] {len(v)} 项:")
                for it in v[:5]:
                    if isinstance(it, dict):
                        txt = it.get("name_zh") or it.get("full_name") or str(it)[:60]
                        print(f"  - {txt}")
                    else:
                        print(f"  - {it}")
            else:
                print(f"\n[{k}] {v}")

    if "sql" in result:
        print(f"\n[生成 SQL] {result['sql']}")


def main():
    print("""
================================================================
  中节能(zjn) 环保执法 RAG+KG 骨架 v1.0
  ─ 知识图谱: 2209 节点 / 8059 关系
  ─ 检索:  图谱 + 向量 + BM25 三路融合 (RRF)
  ─ 模型:  智谱 GLM-4.7-Flash
  ─ 智能体: qa / nl2sql / tracing / compliance / fraud
================================================================
    """)

    print(">>> 加载图谱 / 向量 / BM25 / 嵌入器 ...")
    graph = load_graph()
    chroma = load_vector()
    bm25 = load_bm25(str(KG_DIR / 'logs' / 'chunks.jsonl'), limit=5000)
    embedder = load_embedder()
    if bm25 is None:
        print(">>> [WARNING] BM25 不可用, 仅 graph + vector 检索")
    retriever = HybridRetriever(graph, chroma, bm25, embedder)
    rag = RAGChain(retriever)
    stats = graph.stats()
    print(f">>> 图谱就绪: {stats['nodes_total']} 节点 / {stats['edges_total']} 关系")
    print(f">>> 向量库: {chroma._collection.count() if chroma._collection else 0} 条")
    print(f">>> BM25 语料: {len(bm25.docs) if bm25 else 0} chunks")
    print(f">>> 嵌入器: {embedder.dim} 维 (智谱 embedding-3)")

    demos = [
        # (智能体, 问题, 卖点)
        ("qa", "中华人民共和国大气污染防治法的立法目的是什么？",
         "qa: 答得全, 自动引用 Article 条款"),
        ("qa", "GB 3095-2012 环境空气质量标准中 PM2.5 的年均浓度限值是多少？",
         "qa: 数字型问题, 命中 Standard+Pollutant"),
        ("qa", "超标排放大气污染物的处罚措施有哪些？",
         "qa: 检索 Law + Article + Penalty 三大节点"),
        ("tracing", "SO2 的主要污染源和适用处理技术？",
         "tracing: 大气污染溯源 - 污染物→污染源→技术"),
        ("compliance", "钢铁行业适用什么排放标准？",
         "compliance: 企业合规 - 行业→标准"),
        ("fraud", "企业篡改在线监测数据应该怎么处罚？",
         "fraud: 报告造假 - 检索刑法 286 条 + 案例"),
        ("nl2sql", "河北省有哪些地方标准？",
         "nl2sql: 智能问数 - 图查询 + 区域过滤"),
    ]

    print()
    print(">>> 演示 7 个真实业务问题 ...")
    for i, (skill, q, hint) in enumerate(demos, 1):
        banner(f"演示 {i}/7: [{hint}]")
        try:
            t0 = time.time()
            result = call_skill(skill, rag, {"question": q})
            show_result(skill, q, result, time.time() - t0)
        except Exception as e:
            print(f"[ERROR] {e}")
        if i < len(demos):
            try:
                input("\n>>> 按回车继续下一题 (默认 5s 后自动继续) ...")
            except EOFError:
                import time as _t
                print("[auto-continue] 无输入, 等 5s 后继续")
                _t.sleep(5)

    banner("演示结束")
    print("""
下一步交付物 (按计划 6 月):
  1. Neo4j 切换 (替换 NetworkX 内存图)
  2. ChromaDB 全量 36980 嵌入 (后台进行中, 智谱 API 限流)
  3. 100 题评估集 baseline (recall 58%, 目标 85%)
  4. 5 智能体接入 Dify 编排
  5. FastAPI endpoint v1

联系: 徐简 (Xu Jian)
    """)


if __name__ == "__main__":
    main()
