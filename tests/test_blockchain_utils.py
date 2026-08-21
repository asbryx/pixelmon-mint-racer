import pytest
from pathlib import Path
from src.blockchain_utils import load_rpc_urls, DEFAULT_RPCS

def test_rpc_loading(monkeypatch):
    monkeypatch.delenv("ROBINHOOD_RPC_URLS", raising=False)
    urls = load_rpc_urls()
    assert len(urls) >= 1
    assert any("robinhood" in u for u in urls)

def test_rpc_env_override(monkeypatch):
    test_urls = "https://test-rpc-1.example.com,https://test-rpc-2.example.com"
    monkeypatch.setenv("ROBINHOOD_RPC_URLS", test_urls)
    urls = load_rpc_urls()
    assert urls == ["https://test-rpc-1.example.com", "https://test-rpc-2.example.com"]
