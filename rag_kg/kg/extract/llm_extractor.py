# -*- coding: utf-8 -*-
"""
环保法规领域 LLM 实体关系抽取器
基于 GLM-4.7-Flash (智谱)
特性:
  - 严格 JSON Schema 约束 (受 ontology.yaml 驱动)
  - 自动重试 + 限流处理 (429/1302)
  - 增量写入 (jsonl)
  - 支持中断恢复
  - 实体归一化预处理
"""
import os
import re
import json
import time
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Optional, Generator
from dataclasses import dataclass, asdict

from zai import ZhipuAiClient

import sys
sys.path.insert(0, str(Path(__file__).parent))
from chunker import chunk_datareal

# -----------------------------------------------------------------------------
# 配置
# -----------------------------------------------------------------------------
API_KEY = "f275cb076eab46d697c1285755ab4459.U1t2diOzRAwBYEWm"
MODEL = "glm-4.7-flash"
MAX_TOKENS = 16000
TEMPERATURE = 0.1
REQUEST_INTERVAL = 5  # 秒
MAX_RETRIES = 3

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("kg.extractor")
logger.setLevel(logging.INFO)
fh = logging.FileHandler(LOG_DIR / "extraction.log", encoding="utf-8")
fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(fh)

client = ZhipuAiClient(api_key=API_KEY)


# -----------------------------------------------------------------------------
# Prompt 模板
# -----------------------------------------------------------------------------
SYSTEM_PROMPT = """你是环保执法领域知识图谱构建专家。请从给定的法规/标准/案例文本中, 抽取结构化知识。

## 输出 Schema (严格 JSON, 不要任何额外文字)

```json
{
  "doc_summary": "用 1-2 句话概括本文档主题 (不超过 80 字)",
  "articles": [
    {
      "article_no": "第X条 (含款, 如 '第三十条第一款')",
      "content": "条款完整内容",
      "themes": ["主题1", "主题2"],
      "obligations": ["企业", "政府", "个人"],
      "penalty_text": "本条款涉及的处罚原文 (无则空字符串)"
    }
  ],
  "entities": {
    "Pollutant": [{"name": "污染物名", "category": "大气/水/土壤/固废/噪声"}],
    "PollutionSource": [{"name": "污染源"}],
    "Industry": [{"name": "行业"}],
    "TreatmentTech": [{"name": "治理技术"}],
    "Organization": [{"name": "机构", "level": "国家/省/市/县"}],
    "Region": [{"name": "地区", "level": "国家/省/市/县"}],
    "Violation": [{"name": "违法行为"}],
    "Penalty": [{"name": "处罚措施", "type": "罚款/拘留/停产/吊销/责令改正"}],
    "Case": [{"title": "案例标题", "case_type": "...", "case_time": "YYYY-MM-DD"}]
  },
  "relationships": [
    {"from_type": "Article", "from_id": "article_no", "rel": "ARTICLE_REGULATES", "to_type": "Pollutant", "to_name": "..."},
    {"from_type": "Article", "from_id": "article_no", "rel": "ARTICLE_DEFINES_PENALTY", "to_type": "Penalty", "to_name": "..."},
    {"from_type": "Article", "from_id": "article_no", "rel": "ARTICLE_DEFINES_VIOLATION", "to_type": "Violation", "to_name": "..."},
    {"from_type": "PollutionSource", "from_name": "...", "rel": "SOURCE_EMITS_POLLUTANT", "to_type": "Pollutant", "to_name": "..."},
    {"from_type": "Industry", "from_name": "...", "rel": "INDUSTRY_USES_TECH", "to_type": "TreatmentTech", "to_name": "..."},
    {"from_type": "TreatmentTech", "from_name": "...", "rel": "TECH_TREATS_POLLUTANT", "to_type": "Pollutant", "to_name": "..."},
    {"from_type": "Standard", "from_id": "doc_id", "rel": "STANDARD_LIMITS_POLLUTANT", "to_type": "Pollutant", "to_name": "...", "props": {"limit_value": "数值", "limit_unit": "mg/m³"}}
  ]
}
```

## 规则
1. **articles** 必填, 但若本文档不是法规 (是案例/解读/通知), 留空数组即可
2. entities 每类不超过 20 个
3. relationships 必须端点存在, 不要虚构
4. 同一概念不要重复 (VOCs 和 VOC 合并写 VOCs)
5. **不要臆造**: 不确定就别写
6. JSON 必须用 ```json``` 包裹, 方便解析
"""


def build_user_prompt(chunk_text: str, doc_meta: Dict = None) -> str:
    doc_id = ""
    if doc_meta:
        ca = doc_meta.get("compliance_assessment", {})
        doc_id = ca.get("standard_id") or ca.get("full_name") or ca.get("target_file", "")
        if ca.get("issuing_authority"):
            doc_id += f" (颁布: {ca['issuing_authority'][0]})"
    return f"""## 文档 ID
{doc_id or "未知"}

## 待抽取内容
{chunk_text}

请严格按 Schema 输出 JSON, 不要有任何解释文字。"""


# -----------------------------------------------------------------------------
# 实体归一化
# -----------------------------------------------------------------------------
NORMALIZATION = {
    "Pollutant": {
        # 硫氧化物
        "二氧化硫": "SO2", "SO₂": "SO2", "sulfur dioxide": "SO2", "亚硫酸酐": "SO2", "硫酸酐": "SO2",
        # 氮氧化物
        "氮氧化物": "NOx", "NOX": "NOx", "氮氧化合物": "NOx", "氮氧": "NOx",
        "一氧化氮": "NO", "二氧化氮": "NO2",
        # 颗粒物
        "细颗粒物": "PM2.5", "PM₂.₅": "PM2.5", "细颗粒物(PM2.5)": "PM2.5", "细颗粒物PM2.5": "PM2.5",
        "可吸入颗粒物": "PM10", "PM₁₀": "PM10", "可吸入颗粒物(PM10)": "PM10",
        "总悬浮颗粒物": "TSP", "颗粒物": "PM", "烟尘": "烟尘", "粉尘": "粉尘",
        "降尘": "降尘", "飘尘": "飘尘",
        # 臭氧
        "臭氧": "O3", "O₃": "O3", "光化学烟雾": "O3",
        # VOCs
        "挥发性有机物": "VOCs", "VOC": "VOCs", "VOCs": "VOCs", "非甲烷总烃": "NMHC", "NMHC": "NMHC",
        "甲烷": "CH4", "总烃": "THC",
        # 水污染物 - 常规
        "化学需氧量": "COD", "CODcr": "COD", "CODCr": "COD",
        "生化需氧量": "BOD", "BOD5": "BOD", "BOD₅": "BOD",
        "氨氮": "NH3-N", "NH₃-N": "NH3-N", "氨": "NH3",
        "总氮": "TN", "总磷": "TP", "悬浮物": "SS", "浊度": "浊度",
        "色度": "色度", "pH": "pH", "电导率": "电导率",
        # 水污染物 - 重金属
        "总汞": "Hg", "汞": "Hg", "总镉": "Cd", "镉": "Cd",
        "总铬": "Cr", "六价铬": "Cr6+", "总铅": "Pb", "铅": "Pb",
        "总砷": "As", "砷": "As", "总镍": "Ni", "镍": "Ni",
        "总铜": "Cu", "铜": "Cu", "总锌": "Zn", "锌": "Zn",
        # 土壤污染物
        "石油类": "石油类", "石油烃": "石油类",
        # 固废
        "危险废物": "HW", "危废": "HW", "有害废物": "HW", "危险固废": "HW",
        "一般工业固废": "一般固废", "工业固废": "固废",
        # 噪声
        "噪声": "噪声", "噪音": "噪声", "等效声级": "Leq",
        # 辐射
        "电离辐射": "电离辐射", "电磁辐射": "电磁辐射",
        # 二噁英
        "二噁英": "二噁英", "二恶英": "二噁英", "PCDD": "二噁英",
    },
    "Industry": {
        # 黑色金属
        "钢铁企业": "钢铁", "钢铁行业": "钢铁", "钢铁工业": "钢铁", "黑色金属": "钢铁",
        "炼铁": "钢铁", "炼钢": "钢铁", "烧结": "钢铁", "焦化": "钢铁",
        # 化工
        "化工企业": "化工", "化工行业": "化工", "石油化工": "化工", "石化": "化工",
        "基础化工": "化工", "精细化工": "化工", "煤化工": "化工",
        # 电力
        "火电厂": "火电", "火力发电": "火电", "燃煤电厂": "火电", "燃煤发电": "火电",
        "热电联产": "热电",
        # 建材
        "水泥企业": "水泥", "水泥行业": "水泥", "水泥工业": "水泥", "水泥厂": "水泥",
        "玻璃厂": "玻璃", "陶瓷厂": "陶瓷", "砖瓦": "建材",
        # 纺织
        "印染企业": "印染", "印染行业": "印染", "印染工业": "印染",
        "纺织企业": "纺织", "纺织行业": "纺织",
        "造纸企业": "造纸", "造纸行业": "造纸", "造纸厂": "造纸",
        # 制药
        "制药企业": "制药", "制药行业": "制药", "制药厂": "制药", "医药企业": "制药",
        "化学制药": "制药", "生物制药": "制药",
        # 冶金
        "有色金属": "有色", "有色金属冶炼": "有色", "电解铝": "铝", "铜冶炼": "铜",
        # 采矿
        "矿山": "采矿", "采矿业": "采矿", "选矿": "采矿",
        # 电子
        "电镀企业": "电镀", "电镀厂": "电镀", "电池企业": "电池", "电池厂": "电池",
        "电子企业": "电子", "半导体": "电子",
        # 食品
        "食品企业": "食品", "食品厂": "食品", "酿造": "食品", "屠宰场": "屠宰",
        # 橡胶塑料
        "橡胶企业": "橡胶", "塑料企业": "塑料",
        # 畜禽
        "畜禽养殖": "畜禽", "养殖场": "畜禽", "养殖业": "畜禽", "养猪场": "畜禽",
        # 服务业
        "医院": "医疗", "医疗机构": "医疗", "汽修": "汽车维修",
        # 城市基础设施
        "污水处理厂": "污水处理", "生活垃圾处理": "生活垃圾", "垃圾焚烧厂": "垃圾焚烧",
        "危险废物处置": "危废处置", "危废处理": "危废处置",
    },
    "Region": {
        "中国": "全国", "中华人民共和国": "全国", "全国": "全国",
        "省级": "省", "地市级": "市", "县级": "县",
    },
    "Violation": {
        "违法排污": "超标排放", "超标排污": "超标排放", "超总量排污": "超标排放",
        "违法排放": "超标排放", "超标排污行为": "超标排放",
        "无证经营": "无证经营", "未取得许可证": "无证经营", "未批先建": "未批先建",
        "未依法报批": "未批先建", "未批先建项目": "未批先建",
        "监测数据造假": "数据造假", "伪造数据": "数据造假", "篡改数据": "数据造假",
        "虚假数据": "数据造假", "数据失真": "数据造假",
    },
    "Penalty": {
        "罚款": "罚款", "罚金": "罚款", "处罚款": "罚款",
        "行政拘留": "拘留", "拘留": "拘留",
        "停产整治": "停产", "停产停业": "停产", "停业整顿": "停产",
        "吊销许可证": "吊销", "撤销许可": "吊销",
        "责令改正": "责令改正", "限期改正": "责令改正",
        "按日连续处罚": "按日计罚", "按日处罚": "按日计罚",
        "查封扣押": "查封", "扣押": "查封",
    },
}


def normalize_entity(etype: str, name: str) -> str:
    if etype in NORMALIZATION:
        norm = NORMALIZATION[etype].get(name, name)
        if norm != name:
            return norm
    # 通用: 去空格/全角→半角
    name = name.strip().replace("　", "")
    return name


# -----------------------------------------------------------------------------
# API 调用 (带重试 & 限流处理)
# -----------------------------------------------------------------------------
def call_glm(messages: List[Dict], max_retries: int = MAX_RETRIES) -> Optional[str]:
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                thinking={"type": "disabled"},
            )
            return response.choices[0].message.content
        except Exception as e:
            err = str(e)
            if "429" in err or "1302" in err:
                wait = 30 * (attempt + 1)
                logger.warning(f"限流, 等待 {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"API 错误: {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)
                else:
                    return None
    return None


def extract_json_block(raw: str) -> Optional[str]:
    """从 LLM 输出提取 JSON 块"""
    raw = raw.strip()
    if "```json" in raw:
        m = re.search(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if m:
            return m.group(1)
    if "```" in raw:
        m = re.search(r"```\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if m:
            return m.group(1)
    # 兜底: 找第一个 { 到最后一个 }
    if raw.startswith("{"):
        return raw
    m = re.search(r"(\{.*\})", raw, re.DOTALL)
    return m.group(1) if m else None


# -----------------------------------------------------------------------------
# 数据验证
# -----------------------------------------------------------------------------
def validate_extraction(data: Dict) -> bool:
    """基本结构验证"""
    if not isinstance(data, dict):
        return False
    if "entities" not in data or "relationships" not in data:
        return False
    if not isinstance(data["entities"], dict) or not isinstance(data["relationships"], list):
        return False
    return True


# -----------------------------------------------------------------------------
# 主抽取流程
# -----------------------------------------------------------------------------
@dataclass
class ExtractedDoc:
    doc_id: str
    doc_path: str
    doc_meta: Dict
    raw_text_preview: str
    extraction: Dict
    error: Optional[str] = None


def extract_from_chunk(chunk: Dict) -> ExtractedDoc:
    doc_id = chunk["doc_id"]
    text = chunk["text"]
    meta = chunk.get("doc_metadata", {})

    # 截断: GLM-4.7 支持 200K 上下文, 但为节省 token, 单 chunk 限 12K 字符
    if len(text) > 12000:
        text = text[:12000] + "\n...(后文省略)"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(text, meta)},
    ]
    raw = call_glm(messages)

    if not raw:
        return ExtractedDoc(
            doc_id=doc_id,
            doc_path=chunk.get("source_path", ""),
            doc_meta=meta,
            raw_text_preview=text[:200],
            extraction={},
            error="API call failed",
        )

    json_str = extract_json_block(raw)
    if not json_str:
        return ExtractedDoc(
            doc_id=doc_id,
            doc_path=chunk.get("source_path", ""),
            doc_meta=meta,
            raw_text_preview=text[:200],
            extraction={},
            error="No JSON found",
        )

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return ExtractedDoc(
            doc_id=doc_id,
            doc_path=chunk.get("source_path", ""),
            doc_meta=meta,
            raw_text_preview=text[:200],
            extraction={},
            error=f"JSON decode error: {e}",
        )

    if not validate_extraction(data):
        return ExtractedDoc(
            doc_id=doc_id,
            doc_path=chunk.get("source_path", ""),
            doc_meta=meta,
            raw_text_preview=text[:200],
            extraction={},
            error="Schema validation failed",
        )

    # 实体归一化
    for etype, ents in data.get("entities", {}).items():
        for e in ents:
            if "name" in e:
                e["name"] = normalize_entity(etype, e["name"])

    return ExtractedDoc(
        doc_id=doc_id,
        doc_path=chunk.get("source_path", ""),
        doc_meta=meta,
        raw_text_preview=text[:200],
        extraction=data,
    )


def run_extraction(
    chunks_path: str = None,
    output_path: str = None,
    limit: int = None,
):
    """
    批量抽取
    chunks_path: chunks.jsonl 路径 (None 则实时流式分块)
    output_path: 抽取结果输出
    limit: 处理上限 (测试用)
    """
    output_path = Path(output_path or LOG_DIR / "extractions.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    done_ids = set()
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    done_ids.add(rec.get("doc_id", ""))
                except Exception:
                    pass
        logger.info(f"已处理 {len(done_ids)} 个, 跳过")

    f_out = open(output_path, "a", encoding="utf-8")
    count = 0
    success = 0
    fail = 0

    if chunks_path and Path(chunks_path).exists():
        chunks_iter = (json.loads(line) for line in open(chunks_path, "r", encoding="utf-8"))
    else:
        chunks_iter = chunk_datareal()

    for chunk in chunks_iter:
        if limit and count >= limit:
            break
        if chunk["doc_id"] in done_ids:
            continue

        result = extract_from_chunk(chunk)
        rec = asdict(result)
        f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f_out.flush()
        count += 1

        if result.error:
            fail += 1
            logger.info(f"[{count}] FAIL {result.doc_id}: {result.error}")
        else:
            success += 1
            n_articles = len(result.extraction.get("articles", []))
            n_entities = sum(len(v) for v in result.extraction.get("entities", {}).values())
            n_rels = len(result.extraction.get("relationships", []))
            logger.info(
                f"[{count}] OK {result.doc_id}: {n_articles} articles, {n_entities} entities, {n_rels} rels"
            )

        time.sleep(REQUEST_INTERVAL)

    f_out.close()
    logger.info(f"\n=== 完成 ===\n总数 {count}, 成功 {success}, 失败 {fail}")
    print(f"总数 {count}, 成功 {success}, 失败 {fail}")


if __name__ == "__main__":
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    run_extraction(limit=limit)
