"""mongo 驱动 2-number cap 契约测试 (纯 pipeline, 无 DB)."""
from app.engine.drivers.mongo import MongoDriver

drv = MongoDriver()


def test_pipeline_no_limit_append_default():
    p, applied = drv._apply_mongo_limit([{"$match": {"x": 1}}])
    assert applied == 1000
    assert p[-1] == {"$limit": 1000}


def test_pipeline_limit_below_ceiling_keep():
    p, applied = drv._apply_mongo_limit([{"$match": {"x": 1}}, {"$limit": 5000}])
    assert applied == 5000
    assert p[-1] == {"$limit": 5000}


def test_pipeline_limit_above_ceiling_clamp():
    p, applied = drv._apply_mongo_limit([{"$match": {"x": 1}}, {"$limit": 100000}])
    assert applied == 20000
    assert p[-1] == {"$limit": 20000}


def test_count_pipeline_not_double_counted():
    """LLM 自写 $count → _is_count_pipeline 判定, 不再加 $limit / 不再追加 $count."""
    assert drv._is_count_pipeline([{"$match": {"x": 1}}, {"$count": "count"}]) is True
    assert drv._is_count_pipeline([{"$match": {"x": 1}}, {"$limit": 10}]) is False
