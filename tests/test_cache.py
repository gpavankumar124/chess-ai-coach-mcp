"""Tests for cache.py using a throwaway database (temp_cache fixture)."""

import time


def test_miss_then_hit(temp_cache):
    assert temp_cache.load("t", "k", 60) is None
    temp_cache.save("t", "k", {"hello": "world"})
    assert temp_cache.load("t", "k", 60) == {"hello": "world"}


def test_ttl_zero_is_always_stale(temp_cache):
    temp_cache.save("t", "k", [1, 2, 3])
    assert temp_cache.load("t", "k", 0) is None


def test_ttl_none_never_expires(temp_cache):
    temp_cache.save("t", "k", 42)
    assert temp_cache.load("t", "k", None) == 42


def test_stale_after_ttl(temp_cache, monkeypatch):
    temp_cache.save("t", "k", "v")
    future = time.time() + 3600
    monkeypatch.setattr(temp_cache.time, "time", lambda: future)
    assert temp_cache.load("t", "k", 60) is None


def test_upsert_replaces(temp_cache):
    temp_cache.save("t", "k", "first")
    temp_cache.save("t", "k", "second")
    assert temp_cache.load("t", "k", None) == "second"


def test_clear_by_namespace(temp_cache):
    temp_cache.save("a", "1", 1)
    temp_cache.save("a", "2", 2)
    temp_cache.save("b", "1", 1)
    removed = temp_cache.clear("a")
    assert removed == 2
    assert temp_cache.load("a", "1", None) is None
    assert temp_cache.load("b", "1", None) == 1


def test_stats_counts(temp_cache):
    temp_cache.save("x", "1", 1)
    temp_cache.save("x", "2", 2)
    s = temp_cache.stats()
    assert s["total_entries"] == 2
    assert s["namespaces"]["x"]["entries"] == 2
