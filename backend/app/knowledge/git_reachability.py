"""
Git 仓库可达性校验 + Token 掩码工具

提供:
- mask_token: 将明文 token 转为掩码字符串 (前4 + **** + 后3)
- check_repo_reachable: 使用 git ls-remote 验证仓库可达 + token 有效性
"""

import subprocess

from app.config import settings
from app.knowledge.git_manager import _inject_token


def mask_token(token: str) -> str:
    """
    将明文 token 转为掩码字符串.

    - 空 token → ""
    - 长度 ≤ 7 → "****"
    - 长度 > 7 → "{前4}****{后3}", 如 "ghp_****3abc"
    """
    if not token:
        return ""
    if len(token) <= 7:
        return "****"
    return f"{token[:4]}****{token[-3:]}"


def check_repo_reachable(
    url: str, token: str = "", timeout: int | None = None,
) -> tuple[bool, str]:
    """
    使用 git ls-remote --heads 验证仓库可达且 token 有效.

    timeout 默认 None → 从 settings.git_reachability_timeout_secs 读取 (env: IS_GIT_REACHABILITY_TIMEOUT_SECS, 默认 10).

    返回 (is_reachable, error_message):
    - 成功: (True, "")
    - 认证失败: (False, "Git 访问令牌无效或仓库不存在")
    - 网络不可达: (False, "无法连接 Git 仓库，请检查 URL 和网络")
    - 超时: (False, "Git 仓库连接超时")
    """
    if timeout is None:
        timeout = settings.git_reachability_timeout_secs

    # 构造注入后的 URL (token 为空时返回原始 URL)
    check_url = _inject_token(url, token=token)

    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", check_url],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "Git 仓库连接超时"
    except Exception:
        return False, "无法连接 Git 仓库，请检查 URL 和网络"

    if result.returncode == 0:
        return True, ""

    stderr = result.stderr or ""
    if "Authentication failed" in stderr or "could not read Username" in stderr:
        return False, "Git 访问令牌无效或仓库不存在"

    return False, "无法连接 Git 仓库，请检查 URL 和网络"
