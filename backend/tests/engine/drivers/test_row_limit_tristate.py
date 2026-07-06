"""single/batched/probe 三态行数保护 — 尊重 LLM 意图, 只做保护性干预.

三态: 无 LIMIT 注入 cap / LIMIT>cap 剥离+注入 cap / LIMIT≤cap 保留原值.
锚定 $ 只匹配尾部 LIMIT, 子查询 LIMIT 不动. 生产 trace 60fc1016 回归防线.
"""
from __future__ import annotations

from app.config import settings
from app.engine.drivers.mysql import MySQLDriver


# ── 三态: 无 LIMIT → 注入 cap ──
def test_no_limit_injects_cap():
    out = MySQLDriver._apply_row_limit("SELECT a FROM t WHERE c=1", 1000)
    assert out == "SELECT a FROM t WHERE c=1 LIMIT 1000"


# ── 三态: LIMIT > cap → strip + 注入 cap ──
def test_limit_over_cap_stripped_and_capped():
    out = MySQLDriver._apply_row_limit("SELECT a FROM t LIMIT 10000", 1000)
    assert out.upper().count("LIMIT") == 1
    assert out.endswith("LIMIT 1000")
    assert "10000" not in out


# ── 三态: LIMIT ≤ cap → 保留原值 (尊重 LLM 小样本意图) ──
def test_limit_under_cap_preserved():
    out = MySQLDriver._apply_row_limit("SELECT a FROM t LIMIT 10", 1000)
    assert out == "SELECT a FROM t LIMIT 10"


# ── 边界: LIMIT == cap → 保留 (≤ 分支) ──
def test_limit_equal_cap_preserved():
    out = MySQLDriver._apply_row_limit("SELECT a FROM t LIMIT 1000", 1000)
    assert out == "SELECT a FROM t LIMIT 1000"


# ── LIMIT a,b 形态: 取 b (返回行数) 判定 ──
def test_limit_offset_comma_form_uses_count():
    # LIMIT 5, 10000 → offset=5 count=10000 > cap → strip+cap
    out = MySQLDriver._apply_row_limit("SELECT a FROM t LIMIT 5, 10000", 1000)
    assert out.endswith("LIMIT 1000")
    assert "10000" not in out


# ── 子查询 LIMIT 不被误伤 (末尾非 LIMIT) ──
def test_subquery_limit_not_touched():
    sql = "SELECT * FROM t WHERE id IN (SELECT id FROM u LIMIT 100)"
    out = MySQLDriver._apply_row_limit(sql, 1000)
    assert "LIMIT 100)" in out          # 子查询 100 原样
    assert out.rstrip().endswith("LIMIT 1000")  # 外层注入


# ── _wrap_by_mode 分发到三态 (single/batched/probe) ──
def test_wrap_single_uses_query_row_limit():
    out = MySQLDriver._wrap_by_mode("SELECT a FROM t", "single", 1000)
    assert out.endswith(f"LIMIT {settings.query_row_limit}")


def test_wrap_single_caps_oversized_llm_limit():
    # 生产 bug: single 曾 has_limit 短路放行 10000
    out = MySQLDriver._wrap_by_mode("SELECT a FROM t LIMIT 10000", "single", 1000)
    assert out.endswith(f"LIMIT {settings.query_row_limit}")
    assert "10000" not in out


def test_wrap_probe_uses_probe_row_limit():
    out = MySQLDriver._wrap_by_mode("SELECT a FROM t", "probe", 1000)
    assert out.endswith(f"LIMIT {settings.probe_row_limit}")


def test_wrap_batched_uses_batch_size():
    out = MySQLDriver._wrap_by_mode("SELECT a FROM t", "batched", 500)
    assert out.endswith("LIMIT 500")


# ── count 契约 (方向甲): 收行查询, strip 后包 COUNT 返标量 ──
def test_count_wraps_row_query_into_scalar():
    # LLM 写行查询 (非 COUNT), 驱动包一层 COUNT — 数真实行数
    out = MySQLDriver._wrap_by_mode("SELECT * FROM t WHERE c=1", "count", 1000)
    assert out == "SELECT COUNT(*) AS cnt FROM (SELECT * FROM t WHERE c=1) AS _sub"


def test_count_strips_llm_limit_before_wrapping():
    # 生产 bug 防线: count 不被 LLM 的 LIMIT 封顶 (strip 后包)
    out = MySQLDriver._wrap_by_mode("SELECT * FROM t WHERE c=1 LIMIT 50", "count", 1000)
    assert out == "SELECT COUNT(*) AS cnt FROM (SELECT * FROM t WHERE c=1) AS _sub"
    assert "LIMIT 50" not in out


def test_count_distinct_counts_deduped_values():
    # distinct-count 自然落入常规: 包成 COUNT(*) FROM (SELECT DISTINCT ...)
    out = MySQLDriver._wrap_by_mode("SELECT DISTINCT customer_id FROM t", "count", 1000)
    assert out == (
        "SELECT COUNT(*) AS cnt FROM (SELECT DISTINCT customer_id FROM t) AS _sub"
    )


# ══ 尾注释击穿防线 (spec-review Claim 3: $ 锚定对尾部注释不完备) ══
def test_trailing_line_comment_over_cap_still_capped():
    # 生产击穿: `LIMIT 10000 -- c` 若不剥注释, 注入的 LIMIT 被 -- 吞掉, DB 跑 10000
    out = MySQLDriver._apply_row_limit("SELECT a FROM t LIMIT 10000 -- c", 1000)
    assert out.rstrip().endswith("LIMIT 1000")
    assert "10000" not in out
    assert "--" not in out              # 注释已被 tokenizer 剥除


def test_trailing_block_comment_over_cap_still_capped():
    out = MySQLDriver._apply_row_limit("SELECT a FROM t LIMIT 10000 /* c */", 1000)
    assert out.rstrip().endswith("LIMIT 1000")
    assert "10000" not in out


def test_trailing_comment_no_limit_still_injects_cap():
    # 更广: "无 LIMIT + 尾注释" 若不剥, 注入的 cap 被注释吞 → 全表扫
    out = MySQLDriver._apply_row_limit("SELECT a FROM t -- foo", 1000)
    assert out.rstrip().endswith("LIMIT 1000")
    assert "--" not in out


def test_string_literal_dashes_not_stripped_as_comment():
    # 关键: tokenizer 尊重字符串字面量边界, 引号内 '-- x' 不被当注释剥 (非正则启发式)
    out = MySQLDriver._apply_row_limit("SELECT a, '-- x' AS c FROM t LIMIT 10000", 1000)
    assert "'-- x'" in out              # 字面量原样保留
    assert out.rstrip().endswith("LIMIT 1000")
    assert "10000" not in out


# ── 已知残余边界: 尾部优化器 hint 触发 fail-loud (§3.2, 文档化非阻塞) ──
def test_trailing_optimizer_hint_produces_loud_double_limit():
    # sqlparse 不剥 /*+ hint */; 尾部 hint 破坏 $ 锚定 → 判"无 LIMIT" → 追加 cap →
    # 双 LIMIT 语法错 → 查询显式失败 (fail-loud, 非静默跑 10000). 前置 hint 不受影响。
    out = MySQLDriver._apply_row_limit("SELECT a FROM t LIMIT 10000 /*+ x */", 1000)
    # 畸形形态: 拼出双 LIMIT (DB 层会语法报错, 这里断言 helper 行为可预测非静默正确)
    assert out.count("LIMIT") == 2      # 原 10000 + 追加 1000, 响亮而非静默吞


def test_leading_optimizer_hint_caps_normally():
    # 前置 hint (惯用写法) 不在末尾, 不破坏锚定 → cap 正常
    out = MySQLDriver._apply_row_limit("SELECT /*+ INDEX(t) */ a FROM t LIMIT 10000", 1000)
    assert out.rstrip().endswith("LIMIT 1000")
    assert "10000" not in out


# ── Mongo: probe cap 收编 settings (机制不变, 仅去 hardcode) ──
def test_mongo_probe_limit_reads_settings():
    import inspect

    from app.engine.drivers import mongo as mongo_mod

    src = inspect.getsource(mongo_mod.MongoDriver.execute_query)
    # probe 分支不再硬编码 10, 改读 settings.probe_row_limit
    assert "settings.probe_row_limit" in src
    assert "limit = 10" not in src
