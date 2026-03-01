"""Tests for BinanceLoanClient caching and retry logic."""
import time
from unittest.mock import MagicMock
import pytest

from binance_exporter.client import BinanceLoanClient, BinanceClientError, _COLLATERAL_TTL


def _make_client_no_sdk():
    """Create a BinanceLoanClient instance without initialising the real SDK."""
    client = object.__new__(BinanceLoanClient)
    client._cache = {}
    return client


class TestGetCached:
    STALE_DATA = [{"collateralCoin": "BTC", "liquidationLTV": "0.83"}]
    FRESH_DATA = [{"collateralCoin": "ETH", "liquidationLTV": "0.75"}]

    def test_returns_cached_data_within_ttl(self):
        client = _make_client_no_sdk()
        now = time.monotonic()
        client._cache["collateral"] = (now, self.STALE_DATA)

        fetcher = MagicMock(return_value=self.FRESH_DATA)
        result = client._get_cached(cache_key="collateral", ttl=_COLLATERAL_TTL, fetcher=fetcher)

        assert result == self.STALE_DATA  # returns cached — fetcher not called
        fetcher.assert_not_called()

    def test_refreshes_cache_when_expired(self):
        client = _make_client_no_sdk()
        expired_ts = time.monotonic() - _COLLATERAL_TTL - 1
        client._cache["collateral"] = (expired_ts, self.STALE_DATA)

        fetcher = MagicMock(return_value=self.FRESH_DATA)
        result = client._get_cached(cache_key="collateral", ttl=_COLLATERAL_TTL, fetcher=fetcher)

        assert result == self.FRESH_DATA
        fetcher.assert_called_once()

    def test_stale_cache_returned_when_refresh_fails(self):
        """If a refresh fails and we have stale data, return the stale data."""
        client = _make_client_no_sdk()
        expired_ts = time.monotonic() - _COLLATERAL_TTL - 1
        client._cache["collateral"] = (expired_ts, self.STALE_DATA)

        fetcher = MagicMock(side_effect=BinanceClientError("connection refused", 503))
        result = client._get_cached(cache_key="collateral", ttl=_COLLATERAL_TTL, fetcher=fetcher)

        assert result == self.STALE_DATA  # stale data returned, not raised

    def test_raises_when_no_cache_and_refresh_fails(self):
        """If there is no cached data at all and the fetch fails, propagate the error."""
        client = _make_client_no_sdk()
        # No cache entry for "collateral"

        fetcher = MagicMock(side_effect=BinanceClientError("connection refused", 503))
        with pytest.raises(BinanceClientError):
            client._get_cached(cache_key="collateral", ttl=_COLLATERAL_TTL, fetcher=fetcher)

    def test_empty_response_does_not_overwrite_cache(self):
        """An empty API response should not replace valid cached data."""
        client = _make_client_no_sdk()
        expired_ts = time.monotonic() - _COLLATERAL_TTL - 1
        client._cache["collateral"] = (expired_ts, self.STALE_DATA)

        fetcher = MagicMock(return_value=[])  # empty response
        result = client._get_cached(cache_key="collateral", ttl=_COLLATERAL_TTL, fetcher=fetcher)

        assert result == self.STALE_DATA  # stale data retained
        # Cache should NOT be updated with empty data
        assert client._cache["collateral"][1] == self.STALE_DATA

    def test_empty_response_with_no_cache_returns_empty(self):
        client = _make_client_no_sdk()
        fetcher = MagicMock(return_value=[])
        result = client._get_cached(cache_key="collateral", ttl=_COLLATERAL_TTL, fetcher=fetcher)
        assert result == []
