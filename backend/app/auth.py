"""
认证核心 — JWT + Password + Dependencies
零散的 token 验证是安全的癌症, 统一在此处理
"""

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.metadata import get_db
from app.models.namespace import Namespace
from app.models.user import User, UserNamespaceAccess

# ════════════════════════════════════════════
#  角色层级 — 数值越大权限越高, 中心化判定消除散落字符串比较
# ════════════════════════════════════════════

ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_SUPER_ADMIN = "super_admin"

ROLE_LEVEL: dict[str, int] = {
    ROLE_USER: 0,
    ROLE_ADMIN: 1,
    ROLE_SUPER_ADMIN: 2,
}


def role_at_least(user: "User", role: str) -> bool:
    """user 角色层级 >= 指定 role。未知角色视为最低 (-1)。"""
    return ROLE_LEVEL.get(user.role, -1) >= ROLE_LEVEL[role]


# ════════════════════════════════════════════
#  密码加密
# ════════════════════════════════════════════

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """明文 → 哈希"""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain, hashed)


# ════════════════════════════════════════════
#  JWT Token
# ════════════════════════════════════════════

def create_access_token(user_id: int, role: str) -> str:
    """生成 JWT access token"""
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "sub": str(user_id),  # JWT 规范要求 sub 为字符串
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


# ════════════════════════════════════════════
#  FastAPI Dependencies
# ════════════════════════════════════════════

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    从 JWT token 获取当前用户
    - 401: token 无效/过期/用户不存在
    - 403: 用户被禁用
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exception

    # 查询用户
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception

    # 检查账号状态
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled",
        )

    return user


async def require_admin_or_above(user: User = Depends(get_current_user)) -> User:
    """admin 及以上 (高级管理页准入 / 用户管理)。
    super_admin 的额外特权 (建/管 super_admin·admin 账号) 由端点内
    can_manage + CREATABLE_ROLES 表达, 无需独立 require_super_admin 依赖
    (YAGNI: 当前无纯 super-only 端点)。"""
    if not role_at_least(user, ROLE_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


async def require_super_admin(user: User = Depends(get_current_user)) -> User:
    """仅 super_admin 可访问（Git Token 配置中心等纯 super-only 端点）。"""
    if not role_at_least(user, ROLE_SUPER_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required",
        )
    return user


async def assert_ns_access(db: AsyncSession, user: User, ns_id: int | None) -> None:
    """namespace 作用域断言 (owner∪granted)。super_admin 豁免。无权抛 403。
    用于操作 namespace 内部数据 (查询/知识库/术语/schema/分享…)。

    ns_id 可为 None (如 namespace_id 可空的全局知识条目 / 历史 trace): super_admin
    豁免; 其余角色 owner/granted 查询命中 0 行 → 403 (= 仅 super_admin 可碰无 ns 数据)。"""
    if role_at_least(user, ROLE_SUPER_ADMIN):
        return
    owned = await db.scalar(
        select(Namespace.id).where(
            Namespace.id == ns_id, Namespace.created_by == user.id
        )
    )
    if owned is not None:
        return
    granted = await db.scalar(
        select(UserNamespaceAccess.id).where(
            UserNamespaceAccess.user_id == user.id,
            UserNamespaceAccess.namespace_id == ns_id,
        )
    )
    if granted is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No access to namespace {ns_id}",
        )


async def assert_ns_owner(db: AsyncSession, user: User, ns_id: int) -> None:
    """namespace 归属断言 (仅 owner)。super_admin 豁免。
    用于操作 namespace 本体 (改名/删除); granted-only 的 admin 不能删别人建的 ns。"""
    if role_at_least(user, ROLE_SUPER_ADMIN):
        return
    owned = await db.scalar(
        select(Namespace.id).where(
            Namespace.id == ns_id, Namespace.created_by == user.id
        )
    )
    if owned is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not the owner of namespace {ns_id}",
        )


async def accessible_namespace_ids(db: AsyncSession, user: User) -> list[int] | None:
    """user 可访问的 ns id 列表; super_admin 返 None (= 不过滤, 全部)。"""
    if role_at_least(user, ROLE_SUPER_ADMIN):
        return None
    owned = select(Namespace.id).where(Namespace.created_by == user.id)
    granted = select(UserNamespaceAccess.namespace_id).where(
        UserNamespaceAccess.user_id == user.id
    )
    rows = await db.execute(owned.union(granted))
    return [r[0] for r in rows.all()]


async def require_ns_access(
    ns_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """path {ns_id} + 任意登录角色: ns 作用域读 (如查询历史, user 可达)。"""
    await assert_ns_access(db, user, ns_id)
    return user


async def require_ns_manage(
    ns_id: int,
    user: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
) -> User:
    """path {ns_id} + 管理准入: 高级管理页 P 类端点 (admin+ 且有 ns 权)。"""
    await assert_ns_access(db, user, ns_id)
    return user
