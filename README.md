<div align="center">

# Self-Evolution Data Agent

**A self-evolving, knowledge-driven NL2SQL Agent for multi-source databases — ask your data in plain language, get charts back.**

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

## What is Self-Evolution Data Agent?

**Self-Evolution Data Agent is an open-source NL2SQL agent that turns a plain-language question into a real database query and renders the answer as a chart or table.** A business user types one sentence in natural language; the agent reasons over your schema, runs the query against MySQL or MongoDB, and returns a visualization — no SQL, no BI team, no pre-built data model required.

Unlike a traditional **ChatBI** dashboard or a single-shot **text-to-SQL** translator, Self-Evolution Data Agent is built as an **Agent Loop**: the LLM actively explores your schema, calls tools, validates its own work across multiple reasoning–execution–observation rounds, asks for clarification when it is stuck, and **distills every clarification back into a growing knowledge base**. The more you use it, the more accurate it gets.

### The problem it solves

Most organizations sit on data that only engineers can query. Analysts wait days for a SQL request; BI teams spend weeks pre-modeling dashboards that go stale. Existing NL2SQL tools help, but they break the moment a question falls outside their hand-written examples, and they forget everything between sessions.

Self-Evolution Data Agent removes those bottlenecks:

- **No SQL skills needed** — business users self-serve data by asking in natural language.
- **No BI team, no pre-modeling** — the agent learns your schema and business terms automatically from your code repositories.
- **No vendor lock-in on the model** — swap between Qwen, GPT, DeepSeek, Claude, or a local vLLM / Ollama endpoint via the Web UI (Model Management page); supports hot-switching without restart.
- **Zero intrusion on production** — read-only connections, no schema changes, no plugins, no touching your business code.

---

## Why it beats traditional ChatBI and text-to-SQL

Most NL2SQL products treat the LLM as a passive translation machine: you feed it knowledge, it spits out one SQL string, and if it is wrong you start over. Self-Evolution Data Agent hands the initiative to the LLM and lets it think, recover, and learn.

| Dimension | Traditional ChatBI / text-to-SQL | Self-Evolution Data Agent |
|---|---|---|
| **Who's in control** | LLM passively translates the question into a single SQL string | LLM drives an **Agent Loop**: autonomously probes schema, calls tools, runs multi-round reasoning → execution → observation |
| **When knowledge is missing** | Hits a wall — fabricates wrong SQL or gives up; capability = knowledge boundary | Freely explores and tries; if truly stuck, **proactively asks the user to clarify** instead of failing |
| **Getting better over time** | Clarifications are discarded; the same question stalls again next time | Clarifications are **distilled into knowledge and fed back** — it gets smarter with every question (self-evolving) |
| **Complex / correlated queries** | Crams everything into one giant SQL statement — heavy multi-join / full-table scans risk **blowing up the database** | Generates an **execution plan** and runs it in **decomposed steps**, passing intermediate results between steps and aggregating at the end — no single monster query |
| **Error handling** | Returns failure, user must rephrase | Error-class sliding window → active clarification; **pauses instead of aborting** |
| **Knowledge source** | Hand-written few-shot examples | Multi-channel: **Git source parsing** (cold start) + manual entry + conversation distillation |
| **Memory** | None, starts from zero each time | **A-MEM** memory evolution + recency / adoption-rate decay |
| **Databases** | Usually locked to one SQL dialect | **MySQL + MongoDB** unified under one agent loop; pluggable drivers |
| **Observability** | Black box, not interruptible | End-to-end **SSE streaming** + Langfuse tracing + real-time steering / cancel |

### Four pillars

1. **Agent Loop, not a translator.** A multi-round reasoning-and-acting loop over a toolset replaces single-prompt generation, with quota buckets, dead-loop detection, and forced clarification on error classes as safety nets. For complex correlated questions it builds an **execution plan** and runs it in **decomposed steps** — passing intermediate results between steps and aggregating at the end, instead of firing one monster join that can blow up the database.
2. **Out-of-the-box, built for SMBs.** No BI team, no self-hosted model, zero intrusion on production. Cold-start your schema knowledge automatically from a Git repository and start asking on day one.
3. **A knowledge layer as a moat.** Knowledge is not static few-shot — it's a living asset that auto-bootstraps, is human-reviewed, and self-evolves. Includes a `proposed → canonical` review state machine, A-MEM memory evolution, and HyQE multi-vector recall.
4. **Multi-source, one interaction.** Relational and document databases both run through the same agent loop. `db_type` flows through the whole stack, so adding a new database requires zero changes to the upper layers.

---

## Environments & Deployment

The project has three environments with isolated configuration:

| Environment | Purpose | Runtime | Config File |
|-------------|---------|---------|-------------|
| **dev** | Local development | Bare-metal (uvicorn + vite) | `backend/.env` |
| **test** | Unit tests | `pytest` | `backend/.env.test` |
| **prod** | Production | docker-compose | `backend/.env.prod` |

### dev — Local Development (bare-metal, no Docker)

```bash
cd backend && cp .env.example .env   # fill in real values
cd ../frontend && npm install

# Start in two terminals:
make dev-backend                     # uvicorn :8001 (no --reload, see footgun)
make dev-frontend                    # vite :3000, proxies /api → :8001
```
Visit http://localhost:3000

### test — Unit Tests

```bash
cd backend && cp .env.test.example .env.test   # fill test DB URL (must end in _test)
pip install -e ".[dev]" && pytest
```

### prod — Docker Compose Deployment

```bash
cd backend && cp .env.prod.example .env.prod   # fill prod credentials (use existing DB password)
# PG on host: ensure listen_addresses='*' + pg_hba.conf allows docker subnet + reload
# ChromaDB directory (existing vectors are here; on a fresh machine just mkdir):
mkdir -p /data/chromadb
df -T /data/chromadb                            # verify local fs (ext4/xfs, NOT nfs)

make prod-build      # build backend + frontend images
make prod-up         # start (backend :8000, frontend :80)
make prod-logs       # tail logs
make prod-down       # stop
```
Visit http://<server-ip> (nginx :80 reverse-proxies /api → backend:8000, SSE-capable)

> **Ports**: dev frontend 3000 / backend 8001; prod frontend 80 / backend 8000.
> **PG orchestration**: compose does NOT embed a PG container; prod connects to the host's existing PostgreSQL via `host.docker.internal` (database preserved as-is).
> **ChromaDB**: embedded PersistentClient; prod data is bind-mounted to the host at `IS_CHROMA_HOST_PATH` (default `/data/chromadb`), visible and backuppable on the host.

### Data Backup & Migration

The prod ChromaDB vector store lives on the host at `/data/chromadb` and is **not rebuildable** (recomputing requires full re-embedding). Metadata lives in the host PostgreSQL, backed up separately via `pg_dump` (outside Docker scope).

```bash
# ChromaDB: tar backup (stop writes first to avoid SQLite partial writes)
make prod-down
tar czf chroma-$(date +%Y%m%d).tar.gz -C /data/chromadb .
mkdir -p /data/chromadb && tar xzf chroma-YYYYMMDD.tar.gz -C /data/chromadb   # restore

# Migrate to new host: old host tar → scp to new host → untar to /data/chromadb → make prod-up

# Host PG backup (independent of Docker)
pg_dump self_evolution_data_agent > self_evolution_data_agent-$(date +%Y%m%d).sql
```

> **Filesystem requirement**: `IS_CHROMA_HOST_PATH` must be on a local filesystem (ext4/xfs); NFS/NAS will corrupt ChromaDB's SQLite locking. Verify with `df -T`.

### First query in 4 steps

1. Create a namespace and register a MySQL or MongoDB datasource.
2. Add a Git repository and trigger training — an AI agent autonomously explores the source (any language: Java/Python/Go/...) and extracts your schema and business terms. Optionally pick an extraction **Profile** to guide framework detection.
3. Wait for schema candidates to aggregate, then review and confirm them in the UI.
4. Ask a question in natural language and get a chart back.

---

## Architecture at a glance

```
Natural language → LLM (Agent Loop + tool calls) → DB execution → chart / table
```

| Layer | Responsibility |
|---|---|
| `engine/llm.py` | Unified LLM entry point; routes between OpenAI-compatible (Qwen, GPT, DeepSeek, vLLM, Ollama) and Anthropic (Claude) wire protocols |
| `engine/executor.py` | Full query flow: load datasource → clarify → route engine → safety check → execute → visualize → persist |
| `engine/agent_loop` | Multi-round reasoning-and-acting loop with tool calls, quota buckets, and dead-loop detection |
| `drivers/mysql.py` | MySQL driver (aiomysql + INFORMATION_SCHEMA introspection) |
| `drivers/mongo.py` | MongoDB driver (Motor async + Flavor detection for DocumentDB differences) |
| `knowledge/` | Agentic repo extraction (autonomous agent explores source → emits schema), `proposed → canonical` review state machine, retrieval, HyQE, A-MEM, decay |

### Tech stack

| Area | Stack |
|---|---|
| **Frontend** | React + Vite · SSE streaming client · Vitest / Playwright |
| **Backend** | Python 3.11 + FastAPI + uvicorn · asyncio agent loop |
| **LLM** | OpenAI / Anthropic dual wire protocol · unified `chat_completion` routing · any OpenAI-compatible endpoint |
| **Retrieval** | ChromaDB vector store · Chinese embeddings · layered recall |
| **Metadata** | PostgreSQL 16 · namespaces / knowledge / agent traces |
| **Execution** | Unified agent loop · MySQL (aiomysql) / MongoDB (Motor) drivers |

### Safety guardrails

- **MySQL:** `SELECT`-only, automatic `LIMIT`, `EXPLAIN` row-count pre-check (blocks > 500K rows, warns > 100K).
- **MongoDB:** operation allowlist (`find` / `aggregate` / `count_documents`), forced `$limit`.
- **Agent:** quota buckets + dead-loop detection + forced clarification on error classes + cancellation safety nets.
- **Credentials:** datasource passwords are Fernet-encrypted at rest (field-level `EncryptedString` column type); legacy plaintext rows are transparently migrated on next write.

### Access control (RBAC)

Three-tier role model with namespace-scoped isolation:

| Role | Scope | Capabilities |
|------|-------|--------------|
| **super_admin** | Global | Full system access, user management, all namespaces |
| **admin** | Namespace-owner | Manage owned namespaces, datasources, knowledge, training |
| **user** | Namespace-granted | Query within granted namespaces |

- JWT-based authentication (`IS_JWT_SECRET`), default session 24 h.
- Namespace ownership: creator auto-granted; admins manage their own namespaces.
- Password reset by admin or self-service via profile page.

---

## FAQ

**How is this different from a text-to-SQL library?**
A text-to-SQL library translates one prompt into one SQL string. Self-Evolution Data Agent runs an agent loop that probes the schema, calls tools, validates results, recovers from errors, asks for clarification, and learns from each session.

**Do I need to write SQL?**
No. Business users ask in natural language. The agent generates and executes the query for MySQL or MongoDB.

**Which LLMs are supported?**
Any model behind an OpenAI-compatible or Anthropic-compatible endpoint. Configured via the Web UI (Model Management page) — add your API key, endpoint URL, and model name, then activate. Supports hot-switching without restart.

**Is my production database safe?**
Connections are read-only. The agent never alters table structures, installs plugins, or touches business code. SQL is `SELECT`-only with enforced row limits and pre-execution row-count checks.

**Does it support databases other than MySQL and MongoDB?**
MySQL and MongoDB ship today. The driver layer is pluggable — `db_type` flows through the entire stack, so PostgreSQL, ClickHouse, and others reuse the same upper-layer pipeline.

---

## License

Licensed under the [Apache License 2.0](LICENSE).

---

## Keywords

NL2SQL · Text-to-SQL · ChatBI · natural language to SQL · LLM Agent · Agent Loop · ReAct · RAG · self-evolving knowledge base · MySQL · MongoDB · data visualization · Qwen · Claude · GPT · DeepSeek
