FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple

WORKDIR /app

# ── apt 换腾讯云镜像 + 系统依赖 (git: GitPython 克隆仓库; curl: healthcheck) ──
RUN sed -i 's|deb.debian.org|mirrors.cloud.tencent.com|g; s|security.debian.org|mirrors.cloud.tencent.com|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    git curl \
    && rm -rf /var/lib/apt/lists/*

# ── Oracle Instant Client (Thick mode: 连 Oracle 11g+; Thin mode 仅 12.1+, 旧库报 DPY-3010) ──
#  libaio: Instant Client 链接的系统异步 IO 库. Debian Trixie 64-bit time_t 迁移将
#  libaio.so.1 → libaio.so.1t64, 双 fallback + 条件 symlink 兼容 Bookworm/Trixie.
#  此层独立 (不并入上方 apt 层) 以保依赖层缓存: pyproject 改动不触发 Instant Client 重下.
#  IC 版本参数化 (ARG): 避免源码 x.x.x.x 字面量触发 publish-github 脱敏 IP 正则误报.
ARG IC_MAJOR=21
ARG IC_MINOR=15
RUN apt-get update \
    && (apt-get install -y --no-install-recommends unzip libaio1 \
        || (apt-get install -y --no-install-recommends unzip libaio1t64 \
            && ln -sf libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1)) \
    && curl -fsSL -o /tmp/ic.zip \
        "https://download.oracle.com/otn_software/linux/instantclient/${IC_MAJOR}${IC_MINOR}000/instantclient-basiclite-linux.x64-${IC_MAJOR}.${IC_MINOR}.0.0.0dbru.zip" \
    && unzip -q /tmp/ic.zip -d /opt/oracle \
    && rm /tmp/ic.zip \
    && rm -rf /var/lib/apt/lists/*

#  Thick mode 由 IS_ORACLE_THICK_MODE_LIB_DIR 触发 (oracle.py:_maybe_init_thick_mode).
#  镜像级默认; .env.prod 同值覆盖 (compose env_file 优先级高于 ENV).
ENV IS_ORACLE_THICK_MODE_LIB_DIR=/opt/oracle/instantclient_21_15 \
    LD_LIBRARY_PATH=/opt/oracle/instantclient_21_15

# ── 依赖层 (仅 keyed on pyproject.toml: 改 app 代码不会令此层失效, 跨部署复用缓存) ──
#  只装第三方依赖, 不装本包; 本包留到下一层经 --no-deps 安装 (避免改代码即重装全部依赖)。
COPY pyproject.toml .
RUN python - <<'PY' > /tmp/requirements.txt
import tomllib
deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]
print("\n".join(deps))
PY
RUN pip install -r /tmp/requirements.txt

# ── 应用层 (--no-deps: 依赖已在上一层装好, 此层只构建并安装本包, 不再触网/重装依赖) ──
#  hatchling packages=["app"] 打包需 app/ 就位, 故本包安装须在 COPY app/ 之后。
COPY app/ app/
COPY prompts/ prompts/
RUN pip install --no-deps .

# ── 数据目录 (ChromaDB / repos / backups) ──
RUN mkdir -p /app/data

EXPOSE 8000

# ── 容器健康探针 (compose healthcheck 亦会探, 此为镜像自带兜底) ──
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# 禁止 --reload: ChromaDB PersistentClient 进程级单例, fork 子进程会失联 (见根 CLAUDE.md)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
