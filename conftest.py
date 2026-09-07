"""Shared pytest fixtures and markers.

Sitting at the repo root, this file puts the project modules on ``sys.path`` so
tests can ``import analytics`` etc. directly.
"""

import pytest

import engine


def pytest_configure(config):
    config.addinivalue_line("markers", "network: hits the real Chess.com API")
    config.addinivalue_line("markers", "engine: requires the Stockfish engine")


@pytest.fixture(scope="session")
def stockfish():
    """Skip the test unless Stockfish is actually available."""
    status = engine.engine_available()
    if not status.get("available"):
        pytest.skip(f"Stockfish not available: {status.get('error')}")
    yield
    engine.close_engine()


@pytest.fixture
def temp_cache(tmp_path, monkeypatch):
    """Point the cache module at a throwaway database for the duration of a test."""
    import cache

    monkeypatch.setattr(cache, "DB_PATH", tmp_path / "cache.db")
    return cache
