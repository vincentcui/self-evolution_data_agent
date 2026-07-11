"""三态行数保护 — 尊重 LLM 意图, 只做保护性干预 (现落在 BaseSqlDriver._apply_cap).

三态: 无 LIMIT 注入 default_limit / LIMIT>hard_ceiling 剥离+注入 ceiling / LIMIT≤ceiling 保留原值.
锚定 $ 只匹配尾部 LIMIT, 子查询 LIMIT 不动. 生产 trace 60fc1016 回归防线.

execute-query-cap-contract (2026-07-11): mode 已删, `_wrap_by_mode`/`_apply_row_limit`
静态方法退役 → 三态收敛到 `_apply_cap` (default_limit=1000 / hard_ceiling=20000 两数字).
per-mode 分级 (single/probe/batched) 分级测删除 (功能消失, 新契约由 test_sql_driver_cap_contract 覆盖);
本文件保留尾注释击穿/子查询/字面量等**词法硬化回归防线** (新 cap 契约测未覆盖的独有价值).
"""
from __future__ import annotations

from app.config import settings
from app.engine.drivers.mysql import MySQLDriver

_DRV = MySQLDriver()


# ── 三态: 无 LIMIT → 注入 default_limit ──
def test_no_limit_injects_default():
    sql, applied = _DRV._apply_cap("SELECT a FROM t WHERE c=1")
    assert applied == settings.default_limit
    assert sql == f"SELECT a FROM t WHERE c=1 LIMIT {settings.default_limit}"


# ── 三态: LIMIT > ceiling → strip + 注入 ceiling ──
def test_limit_over_ceiling_stripped_and_capped():
    sql, applied = _DRV._apply_cap("SELECT a FROM t LIMIT 100000")
    assert applied == settings.hard_ceiling
    assert sql.upper().count("LIMIT") == 1
    assert sql.endswith(f"LIMIT {settings.hard_ceiling}")
    assert "100000" not in sql


# ── 三态: LIMIT ≤ ceiling → 保留原值 (尊重 LLM 意图) ──
def test_limit_under_ceiling_preserved():
    sql, applied = _DRV._apply_cap("SELECT a FROM t LIMIT 10")
    assert applied == 10
    assert sql == "SELECT a FROM t LIMIT 10"


# ── 边界: LIMIT == ceiling → 保留 (≤ 分支) ──
def test_limit_equal_ceiling_preserved():
    sql, applied = _DRV._apply_cap(f"SELECT a FROM t LIMIT {settings.hard_ceiling}")
    assert applied == settings.hard_ceiling
    assert sql == f"SELECT a FROM t LIMIT {settings.hard_ceiling}"


# ── LIMIT a,b 形态: 取 b (返回行数) 判定 ──
def test_limit_offset_comma_form_uses_count():
    # LIMIT 5, 100000 → offset=5 count=100000 > ceiling → strip+cap
    sql, applied = _DRV._apply_cap("SELECT a FROM t LIMIT 5, 100000")
    assert applied == settings.hard_ceiling
    assert sql.endswith(f"LIMIT {settings.hard_ceiling}")
    assert "100000" not in sql


# ── 子查询 LIMIT 不被误伤 (末尾非 LIMIT) ──
def test_subquery_limit_not_touched():
    sql, applied = _DRV._apply_cap(
        "SELECT * FROM t WHERE id IN (SELECT id FROM u LIMIT 100)"
    )
    assert "LIMIT 100)" in sql              # 子查询 100 原样
    assert applied == settings.default_limit  # 外层判"无 LIMIT" → 注入 default
    assert sql.rstrip().endswith(f"LIMIT {settings.default_limit}")


# ══ 尾注释击穿防线 (spec-review Claim 3: $ 锚定对尾部注释不完备) ══
def test_trailing_line_comment_over_ceiling_still_capped():
    # 生产击穿: `LIMIT 100000 -- c` 若不剥注释, 注入的 LIMIT 被 -- 吞掉, DB 跑 100000
    sql, applied = _DRV._apply_cap("SELECT a FROM t LIMIT 100000 -- c")
    assert applied == settings.hard_ceiling
    assert sql.rstrip().endswith(f"LIMIT {settings.hard_ceiling}")
    assert "100000" not in sql
    assert "--" not in sql              # 注释已被 tokenizer 剥除


def test_trailing_block_comment_over_ceiling_still_capped():
    sql, applied = _DRV._apply_cap("SELECT a FROM t LIMIT 100000 /* c */")
    assert applied == settings.hard_ceiling
    assert sql.rstrip().endswith(f"LIMIT {settings.hard_ceiling}")
    assert "100000" not in sql


def test_trailing_comment_no_limit_still_injects_default():
    # 更广: "无 LIMIT + 尾注释" 若不剥, 注入的 cap 被注释吞 → 全表扫
    sql, applied = _DRV._apply_cap("SELECT a FROM t -- foo")
    assert applied == settings.default_limit
    assert sql.rstrip().endswith(f"LIMIT {settings.default_limit}")
    assert "--" not in sql


def test_string_literal_dashes_not_stripped_as_comment():
    # 关键: tokenizer 尊重字符串字面量边界, 引号内 '-- x' 不被当注释剥 (非正则启发式)
    sql, applied = _DRV._apply_cap("SELECT a, '-- x' AS c FROM t LIMIT 100000")
    assert "'-- x'" in sql              # 字面量原样保留
    assert applied == settings.hard_ceiling
    assert sql.rstrip().endswith(f"LIMIT {settings.hard_ceiling}")
    assert "100000" not in sql


# ── 已知残余边界: 尾部优化器 hint 触发 fail-loud (§3.2, 文档化非阻塞) ──
def test_trailing_optimizer_hint_produces_loud_double_limit():
    # sqlparse 不剥 /*+ hint */; 尾部 hint 破坏 $ 锚定 → 判"无 LIMIT" → 追加 cap →
    # 双 LIMIT 语法错 → 查询显式失败 (fail-loud, 非静默跑 100000). 前置 hint 不受影响。
    sql, _applied = _DRV._apply_cap("SELECT a FROM t LIMIT 100000 /*+ x */")
    # 畸形形态: 拼出双 LIMIT (DB 层会语法报错, 这里断言 helper 行为可预测非静默正确)
    assert sql.count("LIMIT") == 2      # 原 100000 + 追加 default, 响亮而非静默吞


def test_leading_optimizer_hint_caps_normally():
    # 前置 hint (惯用写法) 不在末尾, 不破坏锚定 → cap 正常
    sql, applied = _DRV._apply_cap("SELECT /*+ INDEX(t) */ a FROM t LIMIT 100000")
    assert applied == settings.hard_ceiling
    assert sql.rstrip().endswith(f"LIMIT {settings.hard_ceiling}")
    assert "100000" not in sql
