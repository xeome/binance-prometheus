import os
import pytest
from binance_exporter.config import ExporterConfig


def test_from_env_raises_on_missing_api_key(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    with pytest.raises(ValueError, match="BINANCE_API_KEY"):
        ExporterConfig.from_env()


def test_from_env_raises_on_missing_api_secret(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "test_key")
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    with pytest.raises(ValueError, match="BINANCE_API_SECRET"):
        ExporterConfig.from_env()


def test_from_env_defaults(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "test_key")
    monkeypatch.setenv("BINANCE_API_SECRET", "test_secret")
    monkeypatch.delenv("EXPORTER_PORT", raising=False)
    monkeypatch.delenv("EXPORTER_HOST", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("REQUEST_TIMEOUT_MS", raising=False)

    config = ExporterConfig.from_env()
    assert config.port == 9090
    assert config.host == "0.0.0.0"
    assert config.log_level == "INFO"
    assert config.request_timeout_ms == 5000


def test_from_env_custom_values(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "mykey")
    monkeypatch.setenv("BINANCE_API_SECRET", "mysecret")
    monkeypatch.setenv("EXPORTER_PORT", "8080")
    monkeypatch.setenv("EXPORTER_HOST", "127.0.0.1")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("REQUEST_TIMEOUT_MS", "10000")

    config = ExporterConfig.from_env()
    assert config.port == 8080
    assert config.host == "127.0.0.1"
    assert config.log_level == "DEBUG"
    assert config.request_timeout_ms == 10000
