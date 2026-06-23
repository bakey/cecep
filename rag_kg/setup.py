# -*- coding: utf-8 -*-
"""
中节能 zjn RAG+KG 骨架 - 一键重建脚本

作用:
  1. 从 DataReal/metadata.json (3384 份) 导入静态层 (Law/Regulation/Standard/Region/Org)
  2. 从 logs/extractions_200.jsonl (LLM 抽取) 导入动态层 (Article/Pollutant/Violation/Penalty/Case)
  3. 重建 NetworkXGraphStore 内存图谱 (目标: 2209 节点 / 8059 关系)
  4. 跳过已存在的 graph_full.json (增量)

数据源:
  - DataReal/  (项目组共享甲方数据, 修改 DATA_ROOT 可调整路径)
  - kg/logs/extractions_200.jsonl  (本包自带, 530KB)

运行:
  python setup.py

输出:
  kg/import_db/graph_store/graph_full.json
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))

KG_DIR = ROOT / "kg"

# 数据源路径 (如不在默认位置, 改成实际路径)
DATA_ROOT = Path(r"E:\Gs_projects\THU_Projects\ChinaEnergyConservation\DataReal")
if not DATA_ROOT.exists():
    DATA_ROOT = ROOT.parent / "DataReal"

EXTRACTIONS = KG_DIR / "logs" / "extractions_200.jsonl"


def main():
    print("=" * 70)
    print("中节能 zjn RAG+KG - 一键重建")
    print("=" * 70)

    if not DATA_ROOT.exists():
        print(f"[ERROR] DataReal 目录不存在: {DATA_ROOT}")
        print("请确认甲方数据是否已挂载/共享到该路径")
        print("或修改本脚本顶部的 DATA_ROOT 变量")
        sys.exit(1)

    if not EXTRACTIONS.exists():
        print(f"[ERROR] 抽取结果不存在: {EXTRACTIONS}")
        print("本包应该包含此文件, 检查是否下载完整")
        sys.exit(1)

    print(f"DataReal: {DATA_ROOT}")
    print(f"抽取结果: {EXTRACTIONS} ({EXTRACTIONS.stat().st_size/1024:.0f} KB)")
    print()

    # 调用 import_extractions.py, 它内部处理 metadata + extractions
    t0 = time.time()
    import subprocess
    cmd = [sys.executable, str(KG_DIR / "import_db" / "import_extractions.py"), str(EXTRACTIONS)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', cwd=str(ROOT))

    print(r.stdout[-2000:] if r.stdout else "")
    if r.returncode != 0:
        print(f"[ERROR] 重建失败:\n{r.stderr[-500:]}")
        sys.exit(1)

    # 验证结果
    from kg.import_db.graph_store import NetworkXGraphStore
    graph_path = KG_DIR / "import_db" / "graph_store" / "graph_full.json"
    if not graph_path.exists():
        print(f"[ERROR] 重建后图谱文件不存在: {graph_path}")
        sys.exit(1)

    g = NetworkXGraphStore()
    g.load(str(graph_path))
    stats = g.stats()

    print()
    print("=" * 70)
    print(f"  重建完成 ✓")
    print(f"  图谱: {stats['nodes_total']} 节点 / {stats['edges_total']} 关系")
    print(f"  节点类型: {stats['by_label']}")
    print(f"  耗时: {time.time()-t0:.1f}s")
    print(f"  图谱文件: {graph_path}")
    print("=" * 70)
    print()
    print("下一步: 跑演示")
    print("  python kg/demo_for_panyh.py")
    print()
    print("或评估检索质量")
    print("  python kg/eval/run_recall_v1.py")


if __name__ == "__main__":
    main()
