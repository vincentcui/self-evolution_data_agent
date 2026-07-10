"""
Git 仓库可达性校验 + Token 掩码工具

提供:
- mask_token: 将明文 token 转为掩码字符串 (前4 + **** + 后3)
- check_repo_reachable: 使用 HTTP 请求验证仓库可达 + token 有效性.
  GitHub URL 走 API (/repos/{owner}/{repo}), 其他 HTTPS URL 走 HEAD 请求.
  不走系统代理 (trust_env=False), 避免代理干扰可达性判断.
"""

import re

import httpx

from app.config import settings
from app.knowledge.git_manager import _inject_token

_GITHUB_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")

# 可达性校验专用 client: 不走系统代理, 避免代理拦截/SSL 错误干扰判断
_http = httpx.Client(trust_env=False)


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


def _check_github_api(url: str, token: str, timeout: int) -> tuple[bool, str]:
    """通过 GitHub API 验证仓库可达性."""
    m = _GITHUB_RE.match(url)
    if not m:
        return False, "无效的 GitHub 仓库 URL"
    owner, repo = m.group(1), m.group(2)

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = _http.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers=headers,
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return False, "Git 仓库连接超时"
    except httpx.ConnectError:
        return False, "无法连接 Git 仓库，请检查 URL 和网络"
    except Exception:
        return False, "无法连接 Git 仓库，请检查 URL 和网络"

    if resp.status_code == 200:
        return True, ""

    if resp.status_code in (401, 403):
        return False, "Git 访问令牌无效或仓库不存在"

    if resp.status_code == 404:
        if token:
            return False, "仓库不存在或无权访问（请检查仓库名是否正确，或 Token 是否对该仓库有权限）"
        return False, "私有仓库需要配置 Git 访问令牌"

    return False, "无法连接 Git 仓库，请检查 URL 和网络"


def _check_generic_https(url: str, token: str, timeout: int) -> tuple[bool, str]:
    """通过 HEAD 请求验证非 GitHub HTTPS 仓库可达性."""
    check_url = _inject_token(url, token=token)
    http_url = check_url.removesuffix(".git")

    try:
        resp = _http.head(http_url, follow_redirects=True, timeout=timeout)
    except httpx.TimeoutException:
        return False, "Git 仓库连接超时"
    except httpx.ConnectError:
        return False, "无法连接 Git 仓库，请检查 URL 和网络"
    except Exception:
        return False, "无法连接 Git 仓库，请检查 URL 和网络"

    if resp.is_success or resp.is_redirect:
        return True, ""

    if resp.status_code in (401, 403):
        return False, "Git 访问令牌无效或仓库不存在"

    if resp.status_code == 404:
        if token:
            return False, "仓库不存在或无权访问（请检查仓库名是否正确，或 Token 是否对该仓库有权限）"
        return False, "私有仓库需要配置 Git 访问令牌"

    if not token and resp.status_code >= 400 and resp.status_code < 500:
        return False, "私有仓库需要配置 Git 访问令牌"

    return False, "无法连接 Git 仓库，请检查 URL 和网络"


def check_repo_reachable(
    url: str, token: str = "", timeout: int | None = None,
) -> tuple[bool, str]:
    """
    验证仓库可达且 token 有效.

    - GitHub URL → 走 API (https://api.github.com/repos/{owner}/{repo}), 稳定可靠
    - 其他 HTTPS URL → 走 HEAD 请求
    - SSH URL → 跳过校验, 返回 True

    timeout 默认 None → 从 settings.git_reachability_timeout_secs 读取.
    """
    if timeout is None:
        timeout = settings.git_reachability_timeout_secs

    if not url.startswith("https://"):
        return True, ""

    if _GITHUB_RE.match(url):
        return _check_github_api(url, token, timeout)

    return _check_generic_https(url, token, timeout)
