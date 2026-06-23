"""快速召回评估 (不需要 GLM API, 只检查检索召回)"""
import sys
import json
import time
from pathlib import Path
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from kg.skeleton_v1 import load_graph, load_bm25, HybridRetriever

KG_DIR = Path(__file__).resolve().parent.parent


def evidence_score(retrieved, expected):
    if not expected:
        return 1.0
    if not retrieved:
        return 0.0
    labels = set()
    for ev in retrieved:
        lbl = ev.get("label", "") or ev.get("metadata", {}).get("doc_type", "")
        labels.add(lbl)
    hits = sum(1 for exp in expected if exp in labels)
    return hits / len(expected)


def main():
    print("加载检索器 (graph + bm25)...")
    graph = load_graph()
    bm25 = load_bm25(str(KG_DIR / 'logs' / 'chunks.jsonl'), limit=10000)
    retriever = HybridRetriever(graph, None, bm25, None)
    print("OK")

    with open(KG_DIR / 'eval' / 'dataset_v1.json', 'r', encoding='utf-8') as f:
        dataset = json.load(f)
    print(f"评估集: {len(dataset)} 题")

    results = []
    type_stats = defaultdict(lambda: {"total": 0, "recall_pass": 0, "sum_recall": 0.0})
    overall = {"total": 0, "recall_pass": 0, "sum_recall": 0.0}

    t0 = time.time()
    for i, item in enumerate(dataset):
        q = item["question"]
        hits = retriever.search(q, top_k=5)
        evidence = hits if hits else []
        recall = evidence_score(evidence, item.get("expected_evidence", []))
        recall_pass = recall >= 0.5

        results.append({
            "id": item["id"],
            "type": item["type"],
            "question": q,
            "recall": round(recall, 3),
            "pass": recall_pass,
            "n_hits": len(hits),
            "sources": list(set(s for h in hits for s in h.get("sources", []))),
        })

        type_stats[item["type"]]["total"] += 1
        type_stats[item["type"]]["recall_pass"] += int(recall_pass)
        type_stats[item["type"]]["sum_recall"] += recall
        overall["total"] += 1
        overall["recall_pass"] += int(recall_pass)
        overall["sum_recall"] += recall

        if (i + 1) % 20 == 0:
            elapsed = time.time() - t0
            print(f"  {i+1}/{len(dataset)} | recall_pass={overall['recall_pass']}/{overall['total']} ({overall['recall_pass']/overall['total']*100:.0f}%) | {elapsed:.0f}s")

    elapsed = time.time() - t0
    n = overall["total"]
    print(f"\n=== 结果 ===")
    print(f"总题数: {n}")
    print(f"召回通过率 (>=0.5): {overall['recall_pass']}/{n} = {overall['recall_pass']/n*100:.1f}%")
    print(f"平均召回分: {overall['sum_recall']/n:.3f}")
    print(f"耗时: {elapsed:.1f}s")
    print()
    print("按类型:")
    for t, s in sorted(type_stats.items()):
        print(f"  {t:25s} | {s['recall_pass']}/{s['total']} pass ({s['recall_pass']/s['total']*100:.0f}%) | avg_recall={s['sum_recall']/s['total']:.3f}")

    report = {
        "timestamp": datetime.now().isoformat(),
        "mode": "graph+bm25_recall_only",
        "total": n,
        "recall_pass_rate": round(overall["recall_pass"] / n, 3),
        "avg_recall": round(overall["sum_recall"] / n, 3),
        "by_type": {t: {
            "total": s["total"],
            "recall_pass_rate": round(s["recall_pass"] / s["total"], 3),
            "avg_recall": round(s["sum_recall"] / s["total"], 3),
        } for t, s in type_stats.items()},
        "details": results,
    }

    out = KG_DIR / 'logs' / f'recall_v1_{datetime.now().strftime("%H%M%S")}.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
