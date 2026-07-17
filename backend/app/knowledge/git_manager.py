"""
Git 仓库管理 — 克隆/更新
浅克隆 --depth 1, 节省磁盘和时间
"""

import os
import shutil
from urllib.parse import urlparse

from git import Repo

from app.config import settings


def clone_or_update(
    url: str,
    branch: str,
    repo_id: int,
    *,
    token: str = "",
) -> tuple[str, str]:
    """
    克隆仓库到本地, 返回 (本地路径, 操作类型)
    操作类型: "pull" | "clone"
    如已存在则 pull 更新

    token: 已按优先级解析的访问令牌 (HTTPS 场景使用), 空字符串表示公开仓库
    """
    local_path = os.path.join(settings.git_clone_dir, str(repo_id))

    # ── 注入 token 到 HTTPS URL ──
    clone_url = _inject_token(url, token=token)

    if os.path.exists(local_path):
        try:
            repo = Repo(local_path)
            # 更新 remote URL (token 可能变更)
            repo.remotes.origin.set_url(clone_url)
            repo.remotes.origin.pull()
            return local_path, "pull"
        except Exception:
            shutil.rmtree(local_path, ignore_errors=True)

    Repo.clone_from(clone_url, local_path, branch=branch, depth=1)
    return local_path, "clone"


def _format_userinfo(host: str, token: str) -> str:
    """
    按 host 推断认证格式 (不绑死具体 host, 多 Git 平台通用).

    - GitLab 实例 (host label 精确含 "gitlab") → "oauth2:{token}"
      覆盖 gitlab.com / gitlab.example.com / corp.gitlab.io; 不误命中 my-gitlab.com / gitlabfoo.com
    - 其他 (github / gitee / 自建非 gitlab) → 裸 "{token}" (与旧逻辑一致, 零回归)
    """
    labels = host.split(".") if host else []
    if "gitlab" in labels:
        return f"oauth2:{token}"
    return token


def _inject_token(url: str, *, token: str = "") -> str:
    """
    将 token 注入 http(s) URL, 按 host 推断认证格式.

      https://github.com/u/r.git      → https://<token>@github.com/u/r.git
      http://gitlab.example.com/u/r.git    → http://oauth2:<token>@gitlab.example.com/u/r.git

    - token 为空时不注入 (公开仓库场景)
    - git@ / ssh:// 抛 ValueError — SSH 协议未启用, 强制 http(s)+token
    - 其他非 http(s) 协议抛 ValueError
    """
    if not token:
        return url

    if url.startswith(("git@", "ssh://", "git+ssh://")):
        raise ValueError("SSH 协议未启用，请使用 http(s)+token 协议访问 Git 仓库")

    if not url.startswith(("http://", "https://")):
        raise ValueError(f"不支持的 Git URL 协议，请使用 http(s):// URL: {url}")

    parsed = urlparse(url)
    # 避免重复注入 (URL 已含凭据)
    if parsed.username:
        return url

    userinfo = _format_userinfo(parsed.hostname or "", token)
    return f"{parsed.scheme}://{userinfo}@{parsed.netloc}{parsed.path}"
