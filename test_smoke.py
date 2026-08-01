"""Smoke test for model-router — verifies routing logic."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from models import RouterConfig, BackendConfig, RouteRule
from router import RouterEngine


def test_default_config():
    r = RouterEngine()
    assert r is not None


def test_resolve_returns_backend():
    r = RouterEngine()
    backend, model = r.resolve("deepseek-chat")
    assert backend is not None
    assert model is not None


def test_route_rule_model():
    rule = RouteRule(
        pattern="deepseek*",
        target="deepseek",
        task_types=["chat"],
    )
    assert rule.pattern == "deepseek*"
    assert "chat" in rule.task_types


if __name__ == "__main__":
    test_default_config()
    test_resolve_returns_backend()
    test_route_rule_model()
    print("✅ all smoke tests passed")
