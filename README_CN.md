<div align="center">

# Self-Evolution Data Agent

**会思考的 NL2SQL Agent · 自演化知识驱动的多源数据库智能问数系统**

一句中文 → 多轮推理 → 直接查库出图，业务人员零 SQL 自助取数。

[English](./README.md) · [中文](./README_CN.md)

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-Vite-61DAFB?logo=react&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-supported-4479A1?logo=mysql&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-supported-47A248?logo=mongodb&logoColor=white)
![LLM](https://img.shields.io/badge/LLM-Qwen%20%7C%20Claude%20%7C%20GPT%20%7C%20DeepSeek-2DD4BF)
![License](https://img.shields.io/badge/License-Apache%202.0-D22128?logo=apache&logoColor=white)

</div>

---

## 这是什么？

**Self-Evolution Data Agent 是一个开源的 NL2SQL 智能问数 Agent：把一句自然语言提问，变成真实的数据库查询，并把结果渲染成图表或表格。** 业务人员用一句中文提问，Agent 自主理解你的库结构，对 MySQL 或 MongoDB 执行查询，再把结果画成图 —— 无需写 SQL，无需 BI 团队，无需预先建模。

与传统的 **ChatBI** 看板或单次 **text-to-SQL** 翻译器不同，Self-Evolution Data Agent 以 **Agent Loop** 为核心：LLM 主动探查 schema、调用工具，在多轮「推理 → 执行 → 观察」中自我验证，卡住时主动向用户澄清，并把**每一次澄清沉淀回不断生长的知识库**。越用越准。

### 解决什么问题

大多数企业的数据只有工程师能查。分析师等几天才能拿到一次 SQL 取数结果；BI 团队花几周预建的看板很快过时。现有的 NL2SQL 工具有帮助，但一旦问题超出手写样例就崩，而且会话之间什么都记不住。

Self-Evolution Data Agent 消除这些瓶颈：

- **零 SQL 门槛** —— 业务人员用自然语言自助取数。
- **无需 BI 团队、无需预建模** —— Agent 从你的代码仓库自动学习 schema 与业务术语。
- **模型自由、不绑死** —— Qwen / GPT / DeepSeek / Claude / 本地 vLLM·Ollama，通过 Web UI（模型管理页面）配置，支持热切换免重启。
- **对生产库零侵入** —— 只读连接、不改表结构、不装插件、不动业务代码。

---

## 相比传统 ChatBI 与 text-to-SQL 的优势

大多数 NL2SQL 产品把 LLM 当作被动的翻译机：人喂知识，它吐一句 SQL，错了就从头再来。Self-Evolution Data Agent 把主动权交给 LLM，让它会思考、会纠错、会沉淀。

| 维度 | 传统 ChatBI / text-to-SQL | Self-Evolution Data Agent |
|---|---|---|
| **主动权在谁** | LLM 被动把问题翻译成一句 SQL | LLM 驱动 **Agent Loop**：自主探查 schema、调用工具、多轮「推理 → 执行 → 观察」 |
| **知识不够时** | 直接抓瞎 —— 编错 SQL 或摆烂；知识边界 = 能力边界 | 自由试错探路，真卡住就**主动请用户澄清**兜底，而非失败 |
| **越用越准** | 澄清信息用完即弃，下次同样的问题再卡一次 | 澄清结论**自动沉淀为知识并回灌** —— 越问越懂你（自演化） |
| **复杂 / 关联查询** | 把所有逻辑塞进一条巨型 SQL —— 重型多表 JOIN / 全表扫描极易**炸库** | 生成**执行计划**并**分步执行**，步骤间传递中间结果、最终汇总 —— 不靠单条怪兽查询 |
| **错误处理** | 失败即返回，用户重述 | 错误类滑窗 → 主动澄清，**暂停而非中止** |
| **知识来源** | 人工手写 few-shot 示例 | 多通道：**Git 源码解析**冷启动 + 人工录入 + 对话沉淀 |
| **记忆机制** | 无，每次从零开始 | **A-MEM** 记忆演化 + recency / 采纳率衰减 |
| **数据库** | 多锁定单一 SQL 方言 | **MySQL + MongoDB** 统一 agent loop · driver 可插拔 |
| **可观测 / 纠偏** | 黑盒，不可中断 | 全程 **SSE 流式** + Langfuse 埋点 + 实时纠偏 / 取消 |

### 四大支柱

1. **Agent Loop，不是翻译器。** 用「多轮推理-行动 + 工具调用」循环取代单次 prompt 直出，配三桶配额、死循环检测、错误类强制澄清多重兜底。面对复杂关联问题，它会先生成**执行计划**再**分步执行** —— 步骤间传递中间结果、最终汇总，而不是甩出一条极易炸库的怪兽 JOIN。
2. **开箱即用，为中小企业而生。** 无需 BI 团队、无需自建模型、对生产库零侵入。从 Git 仓库自动冷启动 schema 知识，首日即可问答。
3. **知识层即护城河。** 知识不是静态 few-shot，而是会生长的资产：自动冷启动、人审兜底、自我演化。含 `proposed → canonical` 审核状态机、A-MEM 记忆演化、HyQE 多向量召回。
4. **多数据源，一套交互。** 关系型与文档型统一走 agent loop，`db_type` 贯穿全栈；接入新库，上层业务零改动。

---

## 环境与部署

项目分三套环境, 配置隔离:

| 环境 | 用途 | 运行方式 | 配置文件 |
|------|------|----------|----------|
| **dev** | 本地开发 | 裸跑 (uvicorn + vite) | `backend/.env` |
| **test** | 单元测试 | `pytest` | `backend/.env.test` |
| **prod** | 生产部署 | docker-compose | `backend/.env.prod` |

### dev — 本地开发 (裸跑, 不进 docker)

```bash
cd backend && cp .env.example .env   # 填入真实值
cd ../frontend && npm install

# 两个终端分别启动:
make dev-backend                     # uvicorn :8001 (禁用 --reload, 见 footgun)
make dev-frontend                    # vite :3000, 代理 /api → :8001
```
访问 http://localhost:3000

### test — 单元测试

```bash
cd backend && cp .env.test.example .env.test   # 填测试库 URL (库名须 _test 结尾)
pip install -e ".[dev]" && pytest
```

### prod — docker-compose 部署

```bash
cd backend && cp .env.prod.example .env.prod   # 填生产真实值 (DB 密码用现库真实值)
# PG 在宿主机: 确保 listen_addresses='*' + pg_hba.conf 放行 docker 网段 + reload
# ChromaDB 目录 (现有向量已在此; 全新机则 mkdir):
mkdir -p /data/chromadb
df -T /data/chromadb                            # 确认本地 fs (ext4/xfs, 非 nfs)

make prod-build      # 构建 backend + frontend 镜像
make prod-up         # 启动 (后端 :8000, 前端 :80)
make prod-logs       # 跟日志
make prod-down       # 停止
```
访问 http://<server-ip> (nginx :80 反代 /api → backend:8000, 含 SSE)

> **端口**: dev 前端 3000 / 后端 8001; prod 前端 80 / 后端 8000。
> **PG 编排**: compose 不内置 PG 容器; prod 经 `host.docker.internal` 连宿主机现有 PostgreSQL (库保留直连)。
> **ChromaDB**: 嵌入式 PersistentClient, prod 数据 bind mount 到宿主机 `IS_CHROMA_HOST_PATH` (默认 `/data/chromadb`), 在宿主可见可备份。

### 数据备份与迁移

prod 的 ChromaDB 向量库落在宿主机 `/data/chromadb`, **不可重建** (重算需全量重 embed)。元数据在宿主机 PostgreSQL, 由 `pg_dump` 单独备份 (不在 docker 范围)。

```bash
# ChromaDB: tar 备份目录 (先停写避免 SQLite 半写)
make prod-down
tar czf chroma-$(date +%Y%m%d).tar.gz -C /data/chromadb .
mkdir -p /data/chromadb && tar xzf chroma-YYYYMMDD.tar.gz -C /data/chromadb   # 恢复

# 换机迁移: 旧机 tar → scp 到新机 → 新机解包到 /data/chromadb → make prod-up

# 宿主机 PG 备份 (与 docker 无关)
pg_dump self_evolution_data_agent > self_evolution_data_agent-$(date +%Y%m%d).sql
```

> **文件系统要求**: `IS_CHROMA_HOST_PATH` 须本地 fs (ext4/xfs); NFS/NAS 会致 ChromaDB SQLite 锁损坏。`df -T` 确认。

### 4 步完成第一次查询

1. 创建 namespace，注册一个 MySQL 或 MongoDB 数据源。
2. 添加 Git 仓库并触发训练 —— Agent 自动解析 schema 与业务术语。
3. 等候 schema 候选汇聚，在界面中审核并确认。
4. 用自然语言提问，拿到图表结果。

---

## 架构速览

```
自然语言 → LLM (Agent Loop + 工具调用) → 数据库执行 → 图表 / 表格
```

| 模块 | 职责 |
|---|---|
| `engine/llm.py` | 统一 LLM 调用入口；OpenAI 兼容协议（Qwen、GPT、DeepSeek、vLLM、Ollama）与 Anthropic 协议（Claude）双线路由 |
| `engine/executor.py` | 查询全流程：加载数据源 → 澄清 → 路由引擎 → 安全检查 → 执行 → 可视化 → 存历史 |
| `engine/agent_loop` | 多轮推理-行动循环，含工具调用、三桶配额、死循环检测 |
| `drivers/mysql.py` | MySQL 引擎（aiomysql + INFORMATION_SCHEMA 探查）|
| `drivers/mongo.py` | MongoDB 引擎（Motor 异步 + Flavor 探测 DocumentDB 差异）|
| `knowledge/` | Git 解析、`proposed → canonical` 审核状态机、检索、HyQE、A-MEM、衰减 |

### 技术栈

| 领域 | 技术 |
|---|---|
| **前端** | React + Vite · SSE 流式客户端 · Vitest / Playwright |
| **后端** | Python 3.11 + FastAPI + uvicorn · asyncio agent loop |
| **LLM** | OpenAI / Anthropic 双线协议 · 统一 `chat_completion` 路由 · 任意 OpenAI 兼容端点 |
| **检索** | ChromaDB 向量库 · 中文 embedding · 分层召回 |
| **元数据** | PostgreSQL 16 · 命名空间 / 知识 / agent traces |
| **执行** | 统一 agent loop · MySQL (aiomysql) / MongoDB (Motor) driver |

### 安全护栏

- **MySQL：** 仅 `SELECT`、自动 `LIMIT`、`EXPLAIN` 行数预检（> 500K 行阻断，> 100K 行警告）。
- **MongoDB：** 操作白名单（`find` / `aggregate` / `count_documents`），强制 `$limit`。
- **Agent：** 三桶配额 + 死循环检测 + 错误类强制澄清 + 多重取消兜底。
- **凭证：** 数据源密码 Fernet 字段级加密落库（`EncryptedString` 列类型），存量明文行下次写入时自动迁移为密文。

### 访问控制（RBAC）

三级角色模型，namespace 作用域隔离：

| 角色 | 作用域 | 能力 |
|------|--------|------|
| **super_admin** | 全局 | 完整系统权限、用户管理、所有 namespace |
| **admin** | Namespace 所有者 | 管理自有 namespace、数据源、知识、训练 |
| **user** | 被授权 namespace | 在被授权的 namespace 内查询 |

- JWT 认证（`IS_JWT_SECRET`），默认会话 24 小时。
- Namespace 所有权：创建者自动获得权限；admin 管理自有 namespace。
- 管理员重置密码 / 用户自助改密（profile 页面）。

---

## 常见问题

**它和一个 text-to-SQL 库有什么区别？**
text-to-SQL 库把一个 prompt 翻译成一句 SQL。Self-Evolution Data Agent 跑的是 agent loop：探查 schema、调用工具、验证结果、从错误中恢复、主动澄清，并从每次会话中学习。

**我需要写 SQL 吗？**
不需要。业务人员用自然语言提问，Agent 自动为 MySQL 或 MongoDB 生成并执行查询。

**支持哪些 LLM？**
支持任何 OpenAI 兼容或 Anthropic 兼容端点的模型。通过 Web UI（模型管理页面）配置 —— 填入 API Key、端点 URL 和模型名，激活即可使用，支持热切换免重启。

**我的生产库安全吗？**
连接为只读。Agent 永不修改表结构、不装插件、不动业务代码。SQL 仅 `SELECT`，并强制行数上限与执行前行数预检。

**除了 MySQL 和 MongoDB 还支持别的库吗？**
目前发布 MySQL 与 MongoDB。driver 层可插拔 —— `db_type` 贯穿全栈，PostgreSQL、ClickHouse 等新库复用同一套上层链路。

---

## 许可证

基于 [Apache License 2.0](LICENSE) 开源。

---

## 关键词 / Keywords

NL2SQL · Text-to-SQL · ChatBI · 自然语言查询数据库 · natural language to SQL · LLM Agent · Agent Loop · ReAct · RAG · 自演化知识库 · MySQL · MongoDB · 智能问数 · 数据可视化 · Qwen · Claude · GPT · DeepSeek
