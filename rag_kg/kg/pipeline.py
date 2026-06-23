# -*- coding: utf-8 -*-
"""
端到端 Pipeline 编排器
执行顺序:
  1. chunk  - 文档分块
  2. extract - LLM 实体关系抽取
  3. import  - 入库 Neo4j
  4. (可选) embed - 向量化
  5. (可选) serve - 启动 RAG 服务
"""
import sys
import argparse
import subprocess
from pathlib import Path

KG_DIR = Path(__file__).parent
sys.path.insert(0, str(KG_DIR))

from loguru import logger  # type: ignore  # 若未安装, 降级

import logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")


def run_module(module: str, *args):
    cmd = [sys.executable, "-m", module] + list(args)
    logging.info(f"运行: {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=str(KG_DIR))
    if r.returncode != 0:
        raise RuntimeError(f"模块 {module} 失败, exit={r.returncode}")


def cmd_chunk(args):
    """文档分块"""
    from extract.chunker import chunk_datareal
    import json
    out = KG_DIR / "logs" / "chunks.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    types = {}
    with open(out, "w", encoding="utf-8") as f:
        for c in chunk_datareal():
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
            count += 1
            types[c["doc_type"]] = types.get(c["doc_type"], 0) + 1
    logging.info(f"✅ 分块完成: {count} chunks, types={types}, output={out}")


def cmd_extract(args):
    """LLM 实体关系抽取"""
    from extract.llm_extractor import run_extraction
    run_extraction(
        chunks_path=str(KG_DIR / "logs" / "chunks.jsonl") if (KG_DIR / "logs" / "chunks.jsonl").exists() else None,
        output_path=str(KG_DIR / "logs" / "extractions.jsonl"),
        limit=args.limit,
    )


def cmd_import(args):
    """入库 Neo4j"""
    cmd = ["import_db/neo4j_importer.py"]
    if args.dry_run:
        cmd.append("--dry-run")
    if args.phase:
        cmd.extend(["--phase", args.phase])
    run_module(*cmd)


def cmd_embed(args):
    """向量化 (SentenceTransformers BGE)"""
    logging.info("TODO: 接入 BGE/SentenceTransformers")
    logging.info("提示: pip install sentence-transformers chromadb")


def cmd_serve(args):
    """启动 RAG API 服务 (FastAPI)"""
    logging.info("TODO: 启动 FastAPI 服务, 见 rag/serve.py")


def cmd_test_skill(args):
    """测试 5 个智能体 Skill"""
    from skills.agent_skills import call_skill
    import json
    skill = args.skill
    if skill == "all":
        for s in ["qa", "nl2sql", "tracing", "compliance", "fraud"]:
            print(f"\n=== {s} ===")
            print(json.dumps(call_skill(s, **{"query": "危险废物非法收集怎么处罚?", "pollutant": "PM2.5", "industry": "钢铁", "data_pattern": "监测值异常", "data_domains": ["钢铁"]}), ensure_ascii=False, indent=2)[:600])
    else:
        print(json.dumps(call_skill(skill, **{k: v for k, v in vars(args).items() if v is not None and k != "skill"}), ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="中节能 KG Pipeline 编排器")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("chunk", help="文档分块").set_defaults(func=cmd_chunk)

    p_ext = sub.add_parser("extract", help="LLM 抽取实体关系")
    p_ext.add_argument("--limit", type=int, default=None)
    p_ext.set_defaults(func=cmd_extract)

    p_imp = sub.add_parser("import", help="入库 Neo4j")
    p_imp.add_argument("--dry-run", action="store_true")
    p_imp.add_argument("--phase", choices=["metadata", "extraction", "all"], default="all")
    p_imp.set_defaults(func=cmd_import)

    sub.add_parser("embed", help="向量化").set_defaults(func=cmd_embed)
    sub.add_parser("serve", help="启动 RAG API").set_defaults(func=cmd_serve)

    p_skill = sub.add_parser("test-skill", help="测试 5 个智能体 Skill")
    p_skill.add_argument("skill", help="qa|nl2sql|tracing|compliance|fraud|all")
    p_skill.add_argument("--query")
    p_skill.add_argument("--pollutant")
    p_skill.add_argument("--industry")
    p_skill.add_argument("--data-pattern")
    p_skill.add_argument("--data-domains")
    p_skill.set_defaults(func=cmd_test_skill)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
