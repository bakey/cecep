# -*- coding: utf-8 -*-
"""
向量嵌入 + ChromaDB 存储
特性:
  - 默认使用智谱 embedding-3 API (2048 维, 零本地 CPU)
  - 可回退到本地 BAAI/bge-small-zh-v1.5 (512 维)
  - 增量写入 ChromaDB
  - 嵌入缓存 (避免重算)
  - 限流重试 (429/1302/1305)
"""
import os
import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Optional
import hashlib

logger = logging.getLogger("kg.embed")

KG_DIR = Path(__file__).resolve().parent.parent
EMBED_DIR = KG_DIR / "embed"
EMBED_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = EMBED_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DIR = EMBED_DIR / "chroma_store"
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

ZHIPU_API_KEY = os.environ.get(
    "ZHIPU_API_KEY", "f275cb076eab46d697c1285755ab4459.U1t2diOzRAwBYEWm"
)
ZHIPU_EMBED_MODEL = "embedding-3"
ZHIPU_EMBED_DIM = 2048
ZHIPU_BATCH_SIZE = 4
ZHIPU_MAX_RETRIES = 3

LOCAL_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
LOCAL_EMBED_DIM = 512
LOCAL_BATCH_SIZE = 64

EMBED_DIM = ZHIPU_EMBED_DIM
BATCH_SIZE = ZHIPU_BATCH_SIZE


def _cache_key(text: str) -> str:
    return "emb_" + hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def _cache_file_name(model_tag: str) -> Path:
    return CACHE_DIR / f"embeddings_{model_tag}.jsonl"


def _load_cache(model_tag: str = "zhipu") -> Dict[str, List[float]]:
    cf = _cache_file_name(model_tag)
    cache = {}
    if cf.exists():
        for line in cf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                cache[rec["k"]] = rec["v"]
            except Exception:
                pass
    return cache


def _save_cache(cache: Dict[str, List[float]], model_tag: str = "zhipu"):
    cf = _cache_file_name(model_tag)
    with open(cf, "w", encoding="utf-8") as f:
        for k, v in cache.items():
            f.write(json.dumps({"k": k, "v": v}, ensure_ascii=False) + "\n")


class ZhipuAPIEmbedder:
    """智谱 embedding-3 API 嵌入器 (零本地 CPU)"""

    def __init__(self, api_key: str = None, model: str = ZHIPU_EMBED_MODEL):
        from zhipuai import ZhipuAI
        self.client = ZhipuAI(api_key=api_key or ZHIPU_API_KEY)
        self.model = model
        self.dim = ZHIPU_EMBED_DIM
        self.cache = _load_cache("zhipu")
        logger.info(f"ZhipuAPIEmbedder 就绪 model={model} dim={self.dim} 缓存={len(self.cache)}")

    def encode(self, texts: List[str], use_cache: bool = True) -> List[List[float]]:
        if not texts:
            return []
        results = [None] * len(texts)
        uncached_idx = []
        uncached_texts = []

        for i, t in enumerate(texts):
            key = _cache_key(t)
            if use_cache and key in self.cache:
                results[i] = self.cache[key]
            else:
                uncached_idx.append(i)
                uncached_texts.append(t)

        if uncached_texts:
            all_embs = []
            for batch_start in range(0, len(uncached_texts), ZHIPU_BATCH_SIZE):
                batch = uncached_texts[batch_start:batch_start + ZHIPU_BATCH_SIZE]
                embs = self._call_api(batch)
                all_embs.extend(embs)
                time.sleep(2.0)

            for idx, emb in zip(uncached_idx, all_embs):
                results[idx] = emb
                key = _cache_key(texts[idx])
                self.cache[key] = emb
            _save_cache(self.cache, "zhipu")
            logger.info(f"ZhipuAPI 编码 {len(uncached_texts)} 条, 缓存总量 {len(self.cache)}")

        return results

    def _call_api(self, texts: List[str]) -> List[List[float]]:
        for attempt in range(10):
            try:
                resp = self.client.embeddings.create(model=self.model, input=texts)
                return [d.embedding for d in resp.data]
            except Exception as e:
                err_str = str(e)
                if any(code in err_str for code in ("429", "1302", "1305", "1113")):
                    wait = min(60 * (attempt + 1), 300)
                    logger.warning(f"限流 {err_str[:60]}, 等待 {wait}s (重试 {attempt+1}/10)")
                    time.sleep(wait)
                else:
                    logger.error(f"embedding API 错误: {e}")
                    raise
        raise RuntimeError(f"embedding API 失败, 已重试 10 次")


# alias
Embedder = ZhipuAPIEmbedder


class LocalEmbedder:
    """BGE 本地嵌入器 (需要 CPU/GPU), 保留兼容"""

    def __init__(self, model_name: str = LOCAL_MODEL_NAME):
        from sentence_transformers import SentenceTransformer
        logger.info(f"加载本地模型: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.dim = self.model.get_sentence_embedding_dimension()
        self.cache = _load_cache("local")
        logger.info(f"LocalEmbedder 就绪 dim={self.dim}, 缓存 {len(self.cache)} 条")

    def encode(self, texts: List[str], use_cache: bool = True) -> List[List[float]]:
        if not texts:
            return []
        results = [None] * len(texts)
        uncached_idx = []
        uncached_texts = []

        for i, t in enumerate(texts):
            key = _cache_key(t)
            if use_cache and key in self.cache:
                results[i] = self.cache[key]
            else:
                uncached_idx.append(i)
                uncached_texts.append(t)

        if uncached_texts:
            logger.info(f"本地编码 {len(uncached_texts)} 条新文本...")
            embs = self.model.encode(
                uncached_texts,
                batch_size=LOCAL_BATCH_SIZE,
                show_progress_bar=False,
                normalize_embeddings=True,
            ).tolist()
            for idx, emb in zip(uncached_idx, embs):
                results[idx] = emb
                key = _cache_key(texts[idx])
                self.cache[key] = emb
            _save_cache(self.cache, "local")

        return results


class ChromaStore:
    """ChromaDB 封装"""

    def __init__(self, persist_dir: str = None, collection_name: str = "kg_chunks"):
        self.persist_dir = persist_dir or str(CHROMA_DIR)
        self.collection_name = collection_name
        self._client = None
        self._collection = None
        try:
            import chromadb
            from chromadb.config import Settings
            self._client = chromadb.PersistentClient(path=self.persist_dir)
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(f"ChromaDB 初始化: {self.persist_dir} (n={self._collection.count()})")
        except Exception as e:
            logger.error(f"ChromaDB 初始化失败: {e}")
            self._collection = None

    def add(self, ids: List[str], texts: List[str], metadatas: List[Dict], embeddings: List[List[float]]):
        if self._collection is None:
            logger.warning("ChromaDB 不可用, 跳过 add")
            return
        self._collection.add(
            ids=ids,
            documents=texts,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        logger.info(f"ChromaDB 添加 {len(ids)} 条")

    def search(self, query_embedding: List[float], top_k: int = 10) -> List[Dict]:
        if self._collection is None:
            return []
        res = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
        )
        hits = []
        for i in range(len(res["ids"][0])):
            hits.append({
                "chunk_id": res["ids"][0][i],
                "text": res["documents"][0][i],
                "metadata": res["metadatas"][0][i],
                "score": 1 - res["distances"][0][i],
                "source": "vector",
            })
        return hits


def build_chunks_index(chunks_path: str, embedder: Embedder, chroma: ChromaStore, limit: int = None, batch_size: int = 500):
    """读取 chunks.jsonl, 分批嵌入后写入 ChromaDB (断点可恢复)"""
    path = Path(chunks_path)
    if not path.exists():
        logger.error(f"chunks 不存在: {path}")
        return

    existing = set()
    if chroma._collection is not None:
        try:
            existing = set(chroma._collection.get()["ids"])
        except Exception:
            existing = set()

    total_done = 0
    total_skip = 0
    ids_batch, texts_batch, metas_batch = [], [], []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            cid = c["chunk_id"]
            if cid in existing:
                total_skip += 1
                continue
            ids_batch.append(cid)
            texts_batch.append(c["text"][:1500])
            metas_batch.append({
                "doc_id": c["doc_id"][:200],
                "doc_type": c.get("doc_type", ""),
                "article_no": c.get("article_no", ""),
                "path": " > ".join(str(x) for x in c.get("structure_path", []))[:300],
                "themes": ",".join(c.get("themes", [])),
            })

            if len(ids_batch) >= batch_size:
                _flush_batch(embedder, chroma, ids_batch, texts_batch, metas_batch)
                total_done += len(ids_batch)
                logger.info(f"进度: {total_done} 已嵌入, {total_skip} 已跳过")
                ids_batch, texts_batch, metas_batch = [], [], []

            if limit and total_done >= limit:
                break

    if ids_batch:
        _flush_batch(embedder, chroma, ids_batch, texts_batch, metas_batch)
        total_done += len(ids_batch)

    logger.info(f"写入完成: {total_done} 新增, {total_skip} 跳过, ChromaDB 总数: {chroma._collection.count()}")


def _flush_batch(embedder, chroma, ids, texts, metas):
    embs = embedder.encode(texts)
    for i in range(0, len(ids), 1000):
        chroma.add(
            ids=ids[i:i+1000],
            texts=texts[i:i+1000],
            metadatas=metas[i:i+1000],
            embeddings=embs[i:i+1000],
        )


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")

    # 1. 构建/加载 chunks
    chunks_jsonl = KG_DIR / "logs" / "chunks.jsonl"
    if not chunks_jsonl.exists():
        print("请先运行 chunker 生成 chunks.jsonl")
        sys.exit(1)

    # 2. 嵌入 + 索引
    emb = Embedder()
    chroma = ChromaStore()

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    build_chunks_index(str(chunks_jsonl), emb, chroma, limit=limit)
    print(f"ChromaDB 总数: {chroma._collection.count() if chroma._collection else 'N/A'}")
