# -*- coding: utf-8 -*-
"""
评估脚本
  - 加载 dataset_v0.json
  - 调用 RAGChain.answer() 跑每个问题
  - 评分 (基于关键词匹配 + 证据覆盖)
  - 输出 baseline 报告

评分规则:
  - 召回 60%: 至少命中一个 expected_evidence 类别
  - 回答 40%: 至少命中 expected_answer_keywords 的 50%
  - 总分 = 0.6 * 召回分 + 0.4 * 回答分
"""
import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

KG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KG_DIR.parent))


def keyword_score(answer: str, keywords: list) -> float:
    """回答关键词命中率: 命中数 / 总关键词数"""
    if not keywords:
        return 1.0
    answer_lower = answer.lower()
    hits = sum(1 for kw in keywords if kw.lower() in answer_lower)
    return hits / len(keywords)


def evidence_score(retrieved_evidence: list, expected_evidence: list) -> float:
    """证据覆盖分: 命中 expected 类别的比例"""
    if not expected_evidence:
        return 1.0
    if not retrieved_evidence:
        return 0.0
    retrieved_labels = set()
    for ev in retrieved_evidence:
        lbl = ev.get("label", "") or ev.get("metadata", {}).get("doc_type", "")
        retrieved_labels.add(lbl)
    hits = sum(1 for exp in expected_evidence if exp in retrieved_labels)
    return hits / len(expected_evidence)


def evaluate_dataset(dataset_path: str, rag_chain, top_k: int = 5) -> dict:
    """跑全量评估"""
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    results = []
    type_stats = defaultdict(lambda: {"total": 0, "recall_pass": 0, "answer_pass": 0, "total_score": 0.0})
    overall = {"total": 0, "recall_pass": 0, "answer_pass": 0, "total_score": 0.0}

    for i, item in enumerate(dataset, 1):
        q = item["question"]
        result = rag_chain.answer(q, top_k=top_k)
        answer = result.get("answer", "")
        evidence = result.get("evidence", [])

        recall = evidence_score(evidence, item.get("expected_evidence", []))
        ans = keyword_score(answer, item.get("expected_answer_keywords", []))
        total = 0.6 * recall + 0.4 * ans
        recall_pass = recall >= 0.5
        ans_pass = ans >= 0.5
        passed = total >= 0.6

        rec = {
            "id": item["id"],
            "type": item["type"],
            "difficulty": item["difficulty"],
            "question": q,
            "answer_preview": answer[:200],
            "recall_score": round(recall, 3),
            "answer_score": round(ans, 3),
            "total_score": round(total, 3),
            "recall_pass": recall_pass,
            "answer_pass": ans_pass,
            "passed": passed,
        }
        results.append(rec)

        # 累计统计
        type_stats[item["type"]]["total"] += 1
        type_stats[item["type"]]["recall_pass"] += int(recall_pass)
        type_stats[item["type"]]["answer_pass"] += int(ans_pass)
        type_stats[item["type"]]["total_score"] += total
        overall["total"] += 1
        overall["recall_pass"] += int(recall_pass)
        overall["answer_pass"] += int(ans_pass)
        overall["total_score"] += total

        # 进度
        if i % 10 == 0 or i == len(dataset):
            print(f"  进度: {i}/{len(dataset)} | 当前 {item['id']} total={total:.3f}")

    # 汇总
    summary = {
        "timestamp": datetime.now().isoformat(),
        "total_questions": overall["total"],
        "recall_pass_rate": round(overall["recall_pass"] / overall["total"], 3),
        "answer_pass_rate": round(overall["answer_pass"] / overall["total"], 3),
        "avg_total_score": round(overall["total_score"] / overall["total"], 3),
        "by_type": {
            t: {
                "total": s["total"],
                "recall_pass_rate": round(s["recall_pass"] / s["total"], 3),
                "answer_pass_rate": round(s["answer_pass"] / s["total"], 3),
                "avg_total_score": round(s["total_score"] / s["total"], 3),
            }
            for t, s in type_stats.items()
        },
        "results": results,
    }
    return summary


def save_report(summary: dict, output_path: str):
    """保存 JSON + Markdown 报告"""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Markdown
    md_path = output_path.replace(".json", ".md")
    lines = [
        f"# 评估 Baseline 报告",
        f"",
        f"**生成时间**: {summary['timestamp']}",
        f"**总题数**: {summary['total_questions']}",
        f"**召回通过率 (≥0.5)**: {summary['recall_pass_rate']*100:.1f}%",
        f"**回答通过率 (≥0.5)**: {summary['answer_pass_rate']*100:.1f}%",
        f"**平均总分**: {summary['avg_total_score']:.3f}",
        f"**KPI 达成 (≥0.85)**: {'✅ 是' if summary['avg_total_score'] >= 0.85 else '❌ 否'}",
        f"",
        f"## 按类型统计",
        f"",
        f"| 类型 | 总数 | 召回通过率 | 回答通过率 | 平均总分 |",
        f"|---|---:|---:|---:|---:|",
    ]
    for t, s in summary["by_type"].items():
        lines.append(f"| {t} | {s['total']} | {s['recall_pass_rate']*100:.1f}% | {s['answer_pass_rate']*100:.1f}% | {s['avg_total_score']:.3f} |")

    lines += [
        f"",
        f"## 失败案例 (总分 < 0.6)",
        f"",
    ]
    for r in summary["results"]:
        if not r["passed"]:
            lines.append(f"- **{r['id']}** ({r['type']}/{r['difficulty']}, score={r['total_score']}): {r['question']}")
            lines.append(f"  - 召回: {r['recall_score']}, 回答: {r['answer_score']}")
            lines.append(f"  - 回答预览: {r['answer_preview'][:150]}")
    lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"报告已保存: {output_path}")
    print(f"Markdown 报告: {md_path}")


if __name__ == "__main__":
    print("=" * 60)
    print("RAG 评估 Baseline")
    print("=" * 60)

    # 加载 RAG
    from kg.skeleton_v1 import load_graph, load_vector, load_bm25, load_embedder, HybridRetriever, RAGChain

    print("\n[加载组件]")
    graph = load_graph()
    chroma = load_vector()
    bm25 = load_bm25(str(KG_DIR / "logs" / "chunks.jsonl"), limit=2000)
    embedder = load_embedder()
    retriever = HybridRetriever(graph, chroma, bm25, embedder)
    rag = RAGChain(retriever)
    print("  ✓ RAG 就绪")

    # 跑评估
    print("\n[跑评估]")
    dataset = str(KG_DIR / "eval" / "dataset_v0.json")
    summary = evaluate_dataset(dataset, rag, top_k=5)

    # 输出
    print("\n" + "=" * 60)
    print("评估结果")
    print("=" * 60)
    print(f"  总题数: {summary['total_questions']}")
    print(f"  召回通过率: {summary['recall_pass_rate']*100:.1f}%")
    print(f"  回答通过率: {summary['answer_pass_rate']*100:.1f}%")
    print(f"  平均总分: {summary['avg_total_score']:.3f}")
    kpi_met = "✅ 是" if summary['avg_total_score'] >= 0.85 else "❌ 否"
    print(f"  KPI 85% 达成: {kpi_met}")

    # 按类型
    print("\n  按类型:")
    for t, s in summary["by_type"].items():
        print(f"    {t:25s} n={s['total']:2d} avg={s['avg_total_score']:.3f}")

    # 保存
    output = KG_DIR / "logs" / f"eval_baseline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    save_report(summary, str(output))
