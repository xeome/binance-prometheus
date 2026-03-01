from __future__ import annotations

import logging
import time
from typing import Any

import backoff
from binance_sdk_crypto_loan.crypto_loan import (
    CryptoLoan,
    ConfigurationRestAPI,
    CRYPTO_LOAN_REST_API_PROD_URL,
)
from binance_common.errors import (
    TooManyRequestsError,
    RateLimitBanError,
    ServerError,
    NetworkError,
)

from binance_exporter.config import ExporterConfig

logger = logging.getLogger(__name__)

# TTLs in seconds
_COLLATERAL_TTL = 3600  # 1 hour — LTV thresholds rarely change
_RATES_TTL = 300        # 5 minutes — interest rates change slowly

# Conservative fallback sleep when Binance rate-limits us.
# The SDK does not expose Retry-After headers on its exception objects,
# so we use a fixed 60-second wait whenever a 429 is encountered.
_RATE_LIMIT_FALLBACK_SLEEP = 60.0


class BinanceClientError(Exception):
    """Raised when Binance API calls fail after all retries."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BinanceLoanClient:
    def __init__(self, config: ExporterConfig) -> None:
        configuration = ConfigurationRestAPI(
            api_key=config.api_key,
            api_secret=config.api_secret,
            base_path=CRYPTO_LOAN_REST_API_PROD_URL,
            retries=0,  # disable SDK-internal retries; backoff owns all retry logic
            timeout=config.request_timeout_ms / 1000,  # SDK expects seconds
        )
        self._client = CryptoLoan(config_rest_api=configuration)
        # Cache storage: {cache_key: (timestamp, data)}
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def get_ongoing_orders(self) -> list[dict[str, Any]]:
        """Fetch all active flexible loan positions. Called live every scrape."""
        return self._call_with_retry(
            lambda: self._client.rest_api.get_flexible_loan_ongoing_orders(limit=100)
        )

    def get_collateral_data(self) -> list[dict[str, Any]]:
        """Get LTV thresholds per collateral coin. Cached for 1 hour."""
        return self._get_cached(
            cache_key="collateral",
            ttl=_COLLATERAL_TTL,
            fetcher=lambda: self._call_with_retry(
                lambda: self._client.rest_api.get_flexible_loan_collateral_assets_data()
            ),
        )

    def get_loan_assets_data(self) -> list[dict[str, Any]]:
        """Get current interest rates per loan coin. Cached for 5 minutes."""
        return self._get_cached(
            cache_key="rates",
            ttl=_RATES_TTL,
            fetcher=lambda: self._call_with_retry(
                lambda: self._client.rest_api.get_flexible_loan_assets_data()
            ),
        )

    def _get_cached(
        self,
        cache_key: str,
        ttl: float,
        fetcher: Any,
    ) -> list[dict[str, Any]]:
        cached = self._cache.get(cache_key)
        now = time.monotonic()
        if cached is not None:
            ts, data = cached
            if now - ts < ttl:
                return data
        try:
            fresh = fetcher()
            if fresh:  # only update cache on non-empty response
                self._cache[cache_key] = (now, fresh)
                return fresh
            # empty response: return stale if available
            if cached is not None:
                logger.warning("Got empty response for %s, using stale cache", cache_key)
                return cached[1]
            return []
        except BinanceClientError:
            if cached is not None:
                logger.warning("Failed to refresh %s, using stale cache", cache_key)
                return cached[1]
            raise  # no cached data at all — propagate

    def _call_with_retry(self, fn: Any) -> list[dict[str, Any]]:
        """
        Execute fn with exponential backoff retry logic.

        - Retries on ServerError (5xx) and NetworkError (connection failures)
        - Sleeps for a fixed 60s when rate-limited (429/451); Binance's
          Retry-After header is not accessible from SDK exceptions
        - Gives up immediately on non-retryable 4xx client errors
        - Raises BinanceClientError after all retries exhausted
        """

        def _is_retryable(e: Exception) -> bool:
            return isinstance(e, (ServerError, NetworkError))

        def _is_rate_limited(e: Exception) -> bool:
            return isinstance(e, (TooManyRequestsError, RateLimitBanError))

        # Mutable container so the inner closure can read/write it
        _rate_limit_sleep: list[float] = [0.0]

        @backoff.on_exception(
            backoff.expo,
            Exception,
            max_tries=3,
            giveup=lambda e: not (_is_retryable(e) or _is_rate_limited(e)),
            on_backoff=lambda details: logger.warning(
                "Retrying Binance API call (attempt %d), waiting %.1fs",
                details["tries"],
                details["wait"],
            ),
        )
        def _do() -> list[dict[str, Any]]:
            if _rate_limit_sleep[0] > 0:
                sleep_secs = _rate_limit_sleep[0]
                logger.warning("Rate limited by Binance, sleeping %ss", sleep_secs)
                time.sleep(sleep_secs)
                _rate_limit_sleep[0] = 0.0

            try:
                response = fn()
                raw = response.data()
                return _normalise_response(raw)
            except Exception as e:
                if _is_rate_limited(e):
                    _rate_limit_sleep[0] = _RATE_LIMIT_FALLBACK_SLEEP
                raise

        try:
            return _do()
        except Exception as e:
            raise BinanceClientError(f"Binance API call failed: {e}") from e


def _normalise_response(raw: Any) -> list[dict[str, Any]]:
    """
    Normalise the various response shapes the Binance SDK returns into a
    flat list of plain dicts.

    The new SDK returns Pydantic model instances; we convert them to dicts
    so the rest of the codebase stays decoupled from SDK model types.

    Note: .data is a callable method on ApiResponse; raw here is already .data()
    — the result of that call, never the ApiResponse itself.
    """
    if raw is None:
        return []

    # Pydantic model with a .rows attribute (ongoing orders, etc.)
    if hasattr(raw, "rows"):
        rows = raw.rows or []
        return [_model_to_dict(r) for r in rows]

    # Plain dict with "rows" or "data" key
    if isinstance(raw, dict):
        if "rows" in raw:
            return raw["rows"] if isinstance(raw["rows"], list) else []
        if "data" in raw:
            return raw["data"] if isinstance(raw["data"], list) else []
        return [raw]

    # Plain list
    if isinstance(raw, list):
        return [_model_to_dict(r) for r in raw]

    # Single Pydantic model — wrap in list
    if hasattr(raw, "model_dump"):
        return [raw.model_dump(by_alias=True)]
    if hasattr(raw, "dict"):
        return [raw.dict(by_alias=True)]

    return []


def _model_to_dict(obj: Any) -> dict[str, Any]:
    """Convert a Pydantic model (or plain dict) to a plain dict."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True)
    if hasattr(obj, "dict"):
        return obj.dict(by_alias=True)
    # Fallback: try __dict__
    return vars(obj)
