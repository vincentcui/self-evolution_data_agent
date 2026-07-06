from app.config import settings


def test_agent_keepalive_default():
    assert hasattr(settings, "agent_keepalive_interval_secs")
    assert settings.agent_keepalive_interval_secs == 30
