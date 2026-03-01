from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExporterConfig:
    api_key: str
    api_secret: str
    port: int
    host: str
    log_level: str
    request_timeout_ms: int

    @classmethod
    def from_env(cls) -> ExporterConfig:
        api_key = os.environ.get("BINANCE_API_KEY", "")
        api_secret = os.environ.get("BINANCE_API_SECRET", "")
        if not api_key:
            raise ValueError("BINANCE_API_KEY environment variable is required")
        if not api_secret:
            raise ValueError("BINANCE_API_SECRET environment variable is required")
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            port=int(os.environ.get("EXPORTER_PORT", "9090")),
            host=os.environ.get("EXPORTER_HOST", "0.0.0.0"),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            request_timeout_ms=int(os.environ.get("REQUEST_TIMEOUT_MS", "5000")),
        )
