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


def _inject_token(url: str, *, token: str = "") -> str:
    """
    将 token 注入 HTTPS URL
    https://github.com/user/repo.git → https://<token>@github.com/user/repo.git

    token 为空时不注入, 返回原始 URL (公开仓库场景)
    """
    if not token:
        return url

    # 只处理 HTTPS URL
    if not url.startswith("https://"):
        return url

    parsed = urlparse(url)
    # 避免重复注入 (URL 已包含 token)
    if parsed.username:
        return url

    # 重构 URL: scheme://token@netloc/path
    return f"{parsed.scheme}://{token}@{parsed.netloc}{parsed.path}"
