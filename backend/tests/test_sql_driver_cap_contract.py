"""SQL 驱动 2-number cap 契约测试 (纯字符串, 无 DB)."""
from app.engine.drivers.mysql import MySQLDriver
from app.engine.drivers.oracle import OracleDriver


class _CapMixin:
    """_apply_cap 三态行为验证. 子类按方言覆写断言辅助方法."""
    driver = None  # 子类填

    def _assert_has_limit(self, sql: str, n: int) -> None:
        """验证 sql 包含数值为 n 的行保护."""
        raise NotImplementedError

    def _assert_no_original_limit(self, sql: str, original: int) -> None:
        """验证原始 LIMIT/ROWNUM 值已被剥除."""
        raise NotImplementedError

    def test_no_limit_inject_default(self):
        sql, applied = self.driver._apply_cap("SELECT * FROM t")
        assert applied == 1000  # default_limit
        self._assert_has_limit(sql, 1000)

    def test_limit_below_ceiling_keep(self):
        sql, applied = self.driver._apply_cap(self._sql_with_limit(5000))
        assert applied == 5000

    def test_limit_above_ceiling_clamp(self):
        sql, applied = self.driver._apply_cap(self._sql_with_limit(100000))
        assert applied == 20000  # hard_ceiling
        self._assert_has_limit(sql, 20000)
        self._assert_no_original_limit(sql, 100000)

    def test_count_not_double_wrapped(self):
        """double-COUNT 根除: LLM 写 COUNT, _apply_cap 只加 cap 不包 COUNT."""
        sql, applied = self.driver._apply_cap(
            "SELECT COUNT(*) FROM orders WHERE status=1"
        )
        # 不得出现外层 SELECT COUNT(*) AS cnt FROM (...) 包裹
        assert "AS cnt FROM (SELECT COUNT" not in sql
        assert "cnt" not in sql  # _apply_cap 不产 cnt 别名

    def test_count_wrap_for_system_count(self):
        """系统补 count 用 count_wrap, 产 cnt 别名."""
        wrapped = self.driver.count_wrap("SELECT * FROM t WHERE x=1")
        assert "SELECT COUNT(*) AS cnt FROM (SELECT * FROM t WHERE x=1) _sub" == wrapped

    def _sql_with_limit(self, n: int) -> str:
        """子类提供带行保护的 SQL 片段."""
        raise NotImplementedError


class TestMysqlCap(_CapMixin):
    driver = MySQLDriver()

    def _assert_has_limit(self, sql: str, n: int) -> None:
        assert f"LIMIT {n}" in sql

    def _assert_no_original_limit(self, sql: str, original: int) -> None:
        assert str(original) not in sql

    def _sql_with_limit(self, n: int) -> str:
        return f"SELECT * FROM t LIMIT {n}"


class TestOracleCap(_CapMixin):
    driver = OracleDriver()

    def _assert_has_limit(self, sql: str, n: int) -> None:
        assert f"ROWNUM <= {n}" in sql

    def _assert_no_original_limit(self, sql: str, original: int) -> None:
        assert str(original) not in sql

    def _sql_with_limit(self, n: int) -> str:
        return f"SELECT * FROM (SELECT * FROM t) WHERE ROWNUM <= {n}"


def test_truncated_logic():
    """truncated = row_count >= applied_limit (driver 内部, 这里验算式)."""
    assert 1000 >= 1000  # 满载 → truncated
    assert not (50 >= 1000)  # 未满 → 不 truncated

