# CECEP 中节能 大模型集成开发平台

> 本仓库由项目组协作维护，每个成员/模块独立子目录。

## 目录结构

| 子目录 | 负责人 | 模块 | 说明 |
|---|---|---|---|
| `rag_kg/` | **徐简** (Xu Jian) | 知识图谱 + 智能问答 (RAG) | 环保执法领域 RAG + KG 骨架 v1 |
| _(待续)_ | 任伟 / 林恒宇 / 潘云泓 / 啸雨 / 梁诗琳 / 明杰 | ... | 各位按需新建子目录 |

## rag_kg/ 快速上手

```powershell
cd rag_kg
pip install -r requirements.txt
python setup.py                  # 重建图谱 (~1s, 图谱文件已包含)
python kg\demo_for_panyh.py      # 7 题业务演示
```

详见 [`rag_kg/Doc/DEMO_README.md`](rag_kg/Doc/DEMO_README.md)。

## 协作约定

- 各子目录互不依赖，**不要跨目录 import**
- 大数据 / 模型权重 / 缓存 **不推** (用各子目录的 `.gitignore`)
- 甲方原始数据 `DataReal/` 共享，不进 Git
- commit 信息约定: `<type>(<scope>): <subject>` (例: `feat(rag_kg): 新增 fraud 智能体`)

## 联系

- 仓库 owner: 潘云泓 (bakey)
- rag_kg 维护: 徐简 (Nemophilistsoda)
