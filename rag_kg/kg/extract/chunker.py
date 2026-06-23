# -*- coding: utf-8 -*-
"""
环保法规文档智能分块器
策略:
  1. Article 优先 - 法规/标准能识别条款则按条款切
  2. Chapter 次之 - 长法典按章节 (Page Index 思想)
  3. 滑窗兜底 - 案例/解读/通知等无结构化文档, 段落级滑窗

输出: chunk dict
  {
    "chunk_id": "...",
    "doc_id": "...",
    "doc_type": "Law|Regulation|Standard|Interpretation|Notice|Case",
    "span_start": int,
    "span_end": int,
    "text": "...",
    "structure_path": ["第二章", "第三节", "第X条"],
    "themes": [...]
  }
"""
import re
import hashlib
from pathlib import Path
from typing import List, Dict, Iterable

# 第X条 (含"第X条第Y款"和纯数字)
ARTICLE_RE = re.compile(
    r"第[一二三四五六七八九十百千零〇0-9]+条"
    r"(?:第[一二三四五六七八九十百千零〇0-9]+款)?"
)
# 第X章 / 第X编 / 第X分编 / 第X节
CHAPTER_RE = re.compile(
    r"^[\s　]*(第[一二三四五六七八九十百千零〇0-9]+(?:章|编|分编|节))"
    r"[\s　]*([^\n]{0,40})",
    re.MULTILINE
)

# 条款级主题启发式关键词
THEME_KEYWORDS = {
    "超标排放": ["超标", "超过排放标准", "超过限值"],
    "危险废物": ["危险废物", "危废", "HW"],
    "排污许可": ["排污许可证", "排污许可", "无证排污"],
    "监测数据": ["监测数据", "在线监测", "自动监测", "虚假数据", "伪造"],
    "处罚": ["罚款", "拘留", "停产", "吊销", "责令改正"],
    "刑事责任": ["刑事责任", "刑事处罚", "构成犯罪"],
    "环评": ["环境影响评价", "环评", "未批先建"],
    "应急预案": ["突发环境事件", "应急预案"],
}


def make_chunk_id(doc_id: str, span_start: int, span_end: int) -> str:
    raw = f"{doc_id}::{span_start}::{span_end}"
    return "chunk_" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def detect_themes(text: str) -> List[str]:
    themes = []
    for theme, kws in THEME_KEYWORDS.items():
        for kw in kws:
            if kw in text:
                themes.append(theme)
                break
    return themes


def detect_doc_type(text: str, metadata: Dict = None) -> str:
    """根据内容 + metadata 判断文档类型"""
    if metadata and "compliance_assessment" in metadata:
        ca = metadata["compliance_assessment"]
        full = ca.get("full_name", "") or ca.get("target_file", "")
        if "条例" in full or "法" == full[-1:]:
            return "LawOrRegulation"
        if any(full.startswith(p) for p in ["GB ", "HJ ", "DB "]):
            return "Standard"
    # 启发式
    if re.search(r"^[^\n]*第[一二三四五六七八九十]+条[^\n]{0,3}$", text[:3000], re.M):
        return "LawOrRegulation"
    if any(t in text[:200] for t in ["标准", "限值", "mg/m³"]):
        return "Standard"
    return "Other"


def chunk_by_article(text: str, doc_id: str) -> List[Dict]:
    """按 Article 切分, 保留章节路径"""
    chunks = []
    matches = list(ARTICLE_RE.finditer(text))
    if not matches:
        return []

    # 提取章节锚点
    chapters = [(m.start(), m.group(1), m.group(2).strip()) for m in CHAPTER_RE.finditer(text)]

    def find_chapter(pos: int) -> str:
        cur = ""
        for cpos, cname, _ in chapters:
            if cpos <= pos:
                cur = cname
            else:
                break
        return cur

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk_text = text[start:end].strip()
        if len(chunk_text) < 20:
            continue
        chunks.append({
            "chunk_id": make_chunk_id(doc_id, start, end),
            "doc_id": doc_id,
            "doc_type": "Article",
            "span_start": start,
            "span_end": end,
            "text": chunk_text,
            "structure_path": [find_chapter(start), m.group(0)],
            "article_no": m.group(0),
            "themes": detect_themes(chunk_text),
        })
    return chunks


def chunk_by_chapter(text: str, doc_id: str, min_len: int = 500) -> List[Dict]:
    """按章节切 (长法典兜底)"""
    chapters = list(CHAPTER_RE.finditer(text))
    if len(chapters) < 2:
        return []

    chunks = []
    for i, m in enumerate(chapters):
        start = m.start()
        end = chapters[i + 1].start() if i + 1 < len(chapters) else len(text)
        chunk_text = text[start:end].strip()
        if len(chunk_text) < min_len:
            continue
        chunks.append({
            "chunk_id": make_chunk_id(doc_id, start, end),
            "doc_id": doc_id,
            "doc_type": "Chapter",
            "span_start": start,
            "span_end": end,
            "text": chunk_text,
            "structure_path": [m.group(1) + " " + m.group(2).strip()],
            "themes": detect_themes(chunk_text),
        })
    return chunks


def chunk_by_window(
    text: str, doc_id: str, window: int = 800, overlap: int = 150
) -> List[Dict]:
    """段落级滑窗 (案例/解读/无结构文档)"""
    # 先按段落切
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks = []
    cur = []
    cur_len = 0
    span_start = 0
    for p in paragraphs:
        if cur_len + len(p) > window and cur:
            text_chunk = "\n\n".join(cur)
            chunks.append({
                "chunk_id": make_chunk_id(doc_id, span_start, span_start + len(text_chunk)),
                "doc_id": doc_id,
                "doc_type": "Window",
                "span_start": span_start,
                "span_end": span_start + len(text_chunk),
                "text": text_chunk,
                "structure_path": ["N/A"],
                "themes": detect_themes(text_chunk),
            })
            # 滑窗重叠
            while cur and cur_len > overlap:
                cur.pop(0)
                cur_len = sum(len(x) for x in cur)
            span_start += len(text_chunk) - sum(len(x) for x in cur)
            cur.append(p)
            cur_len += len(p)
        else:
            cur.append(p)
            cur_len += len(p)

    if cur:
        text_chunk = "\n\n".join(cur)
        chunks.append({
            "chunk_id": make_chunk_id(doc_id, span_start, span_start + len(text_chunk)),
            "doc_id": doc_id,
            "doc_type": "Window",
            "span_start": span_start,
            "span_end": span_start + len(text_chunk),
            "text": text_chunk,
            "structure_path": ["N/A"],
            "themes": detect_themes(text_chunk),
        })
    return chunks


def chunk_document(text: str, doc_id: str, doc_type_hint: str = None) -> List[Dict]:
    """
    智能分块主入口:
      1. 若 Article 数 >= 3, 用 Article 切
      2. 否则若 Chapter 数 >= 2 且文本 > 5000, 用 Chapter 切
      3. 否则用滑窗
    """
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    article_chunks = chunk_by_article(text, doc_id)
    if len(article_chunks) >= 3:
        return article_chunks

    if len(text) > 5000:
        chapter_chunks = chunk_by_chapter(text, doc_id)
        if len(chapter_chunks) >= 2:
            return chapter_chunks

    return chunk_by_window(text, doc_id)


def chunk_datareal(data_root: str = None,
                   output_path: str = None) -> Iterable[Dict]:
    """遍历 DataReal, 逐文档分块, 返回 chunk 迭代器"""
    import json
    import os
    from pathlib import Path

    if data_root is None:
        env_p = os.environ.get("ZJN_DATA_ROOT")
        if env_p:
            data_root = env_p
        else:
            cand = Path(__file__).resolve().parent.parent.parent / "DataReal"
            data_root = str(cand if cand.exists() else Path(__file__).resolve().parent / "DataReal")
    data_root = Path(data_root)
    output_path = Path(output_path) if output_path else None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(output_path, "w", encoding="utf-8")
        f.write("[\n")
        first = True

    for md_path in data_root.rglob("*.md"):
        meta_path = md_path.parent / "metadata.json"
        meta = {}
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as fp:
                    meta = json.load(fp)
            except Exception:
                meta = {}

        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception:
            try:
                raw = open(str(md_path), "r", encoding="utf-8", errors="replace")
                text = raw.read()
                raw.close()
            except Exception as e2:
                try:
                    import os
                    abs_p = os.path.abspath(str(md_path))
                    long_p = "\\\\?\\" + abs_p if not abs_p.startswith("\\\\") else abs_p
                    with open(long_p, "r", encoding="utf-8", errors="replace") as fp:
                        text = fp.read()
                except Exception as e3:
                    print(f"[WARN] skip {md_path}: {e3}")
                    continue

        doc_id = md_path.parent.name
        ca = meta.get("compliance_assessment", {}) or {}
        std_id_raw = ca.get("standard_id") or ""
        std_id = std_id_raw.strip() if isinstance(std_id_raw, str) else ""
        doc_type = "Case" if "案例" in str(data_root) else (
            "Standard" if std_id else "LawOrRegulation"
        )

        chunks = chunk_document(text, doc_id, doc_type)

        for c in chunks:
            c["doc_metadata"] = ca
            c["source_path"] = str(md_path)
            if output_path:
                if not first:
                    f.write(",\n")
                f.write(json.dumps(c, ensure_ascii=False))
                first = False
            yield c

    if output_path:
        f.write("\n]\n")
        f.close()


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "logs" / "chunks.jsonl")
    # 改成 JSONL (一行一个 chunk)
    out_jl = out.replace(".json", ".jsonl") if out.endswith(".json") else out + ".jsonl"

    count = 0
    types = {}
    with open(out_jl, "w", encoding="utf-8") as f:
        for c in chunk_datareal():
            f.write(__import__("json").dumps(c, ensure_ascii=False) + "\n")
            count += 1
            types[c["doc_type"]] = types.get(c["doc_type"], 0) + 1
    print(f"Total chunks: {count}")
    print(f"By type: {types}")
    print(f"Output: {out_jl}")
