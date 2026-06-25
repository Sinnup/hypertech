"""
Tests for HT-CHROMA: KB ingestion robustness.

Unit-level (no ChromaDB server): verify the placeholder service-account
detection and that chroma.query / chroma.health degrade gracefully when the
server is unreachable instead of raising into the pipeline.
"""

import core.memory.chroma as chroma
from ingestion.google_drive.ingest import _is_placeholder_sa


def test_placeholder_sa_detection():
    assert _is_placeholder_sa("") is True
    assert _is_placeholder_sa("path/to/service-account.json") is True
    assert _is_placeholder_sa("service-account.json") is True
    assert _is_placeholder_sa("/Users/me/secrets/sa-real.json") is False


def test_query_unreachable_returns_empty(monkeypatch):
    def _boom():
        raise ConnectionError("chroma down")
    monkeypatch.setattr(chroma, "get_collection", _boom)
    assert chroma.query("anything") == []          # never raises
    assert chroma.count() == 0


def test_health_reports_unreachable(monkeypatch):
    def _boom():
        raise ConnectionError("chroma down")
    monkeypatch.setattr(chroma, "get_collection", _boom)
    h = chroma.health()
    assert h["reachable"] is False
    assert h["count"] == 0
    assert "error" in h
