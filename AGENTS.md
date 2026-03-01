# AGENTS

## Purpose

Prometheus exporter for Binance flexible crypto loans. Scrapes live loan positions, cached collateral thresholds, and interest rates; exposes them as Gauges on an HTTP `/metrics` endpoint.

## Module Map

| File | Responsibility |
|------|----------------|
| `src/binance_exporter/config.py` | `ExporterConfig` frozen dataclass; `from_env()` validates and loads env vars; raises `ValueError` on missing API credentials |
| `src/binance_exporter/client.py` | `BinanceLoanClient` — wraps Binance SDK; owns all retry logic and in-process TTL caching; normalises every SDK response shape to `list[dict]` |
| `src/binance_exporter/collector.py` | `LoanCollector(Collector)` — creates all metric families fresh on every `collect()` call; derives liquidation price for stablecoin loans only |
| `src/binance_exporter/cli.py` | Entry point; loads `.env`, applies CLI arg overrides over env config, unregisters default collectors, registers `LoanCollector`, starts HTTP server |
| `src/binance_exporter/__main__.py` | Three-line trampoline enabling `python -m binance_exporter` |
| `tests/` | pytest; Binance client mocked via `MagicMock`; `collect_to_dict()` helper maps `collect()` output to `{metric_name: samples}` |

## Scrape Lifecycle

```
CLI → BinanceLoanClient → LoanCollector registered
           ↓  on each /metrics request
  collector.collect()
    ├── get_ongoing_orders()     # live, every scrape — gates binance_up
    ├── get_collateral_data()    # cached 1 h
    ├── get_loan_assets_data()   # cached 5 min
    └── yield 12 MetricFamily objects (always, even on partial failure)
```

## Cache Behaviour

| Method | TTL | On error |
|--------|-----|----------|
| `get_ongoing_orders()` | none | propagates as `BinanceClientError` |
| `get_collateral_data()` | 3600 s | serves stale if available; raises only with no prior cache |
| `get_loan_assets_data()` | 300 s | serves stale if available; raises only with no prior cache |

Uses `time.monotonic()`. Empty API responses are rejected — stale data is preferred over empty.

## Retry / Error Handling

- `_call_with_retry()`: `backoff.expo`, `max_tries=3`
- Retries on: `ServerError` (5xx), `NetworkError`
- Rate-limit (`TooManyRequestsError`, `RateLimitBanError`): 60 s fixed sleep before retry (SDK does not expose `Retry-After`)
- Non-retryable 4xx: immediate `giveup`
- SDK configured with `retries=0` — backoff owns all retry logic exclusively
- All failures wrap to `BinanceClientError(message, status_code=None)`

## Invariants — MUST NOT Break

1. **`describe()` ↔ `collect()` name parity** — metric names in `describe()` must exactly match what `collect()` yields; `prometheus_client` panics on mismatches.
2. **Fresh families per scrape** — all `GaugeMetricFamily` objects are instantiated inside `collect()`; never lift them to instance or class state.
3. **Stablecoin guard** — `binance_loan_liquidation_price_derived` is only emitted when `loan_coin in STABLECOIN_SET`; the formula `totalDebt / (collateralAmount × liquidationLTV)` is numerically invalid for non-USD-pegged loans.
4. **Health metrics always yield** — `binance_up` and `binance_scrape_duration_seconds` must be emitted on every scrape, including when all API calls fail.
5. **Zero-denominator guard** — `_derive_liquidation_price` returns `None` when `collateral_amount <= 0` or `liquidation_ltv <= 0`; never remove this check.

## Dev Workflow

```bash
pip install -e ".[dev]"          # install package + dev extras
pytest                            # run all tests
pytest --cov=binance_exporter    # with coverage report
cp .env.example .env && $EDITOR .env
python -m binance_exporter        # run locally
docker compose up -d              # run with bundled Prometheus (port 9091)
```

## Patterns

**Adding a new metric**
1. Declare in `describe()` as a `GaugeMetricFamily` with the target name.
2. Instantiate fresh at the top of `collect()`.
3. Call `add_metric(labels, value)` in the appropriate step.
4. `yield` it at the bottom of `collect()`.
5. Add a `test_describe_names_match_collect` assertion guard (already present — it will catch any drift automatically).

**Naming convention**
- `binance_loan_<thing>` — per-position metrics, labels `[loan_coin, collateral_coin]`
- `binance_loan_<thing>_threshold` — collateral-level metrics, label `[collateral_coin]`
- `binance_loan_flexible_<thing>` — loan-coin-level metrics, label `[loan_coin]`
- `binance_<thing>` — exporter health metrics, no labels

**Adding a new Binance API call**
1. Add a public method to `BinanceLoanClient`.
2. For cacheable data use `_get_cached(cache_key, ttl, fetcher)`.
3. Pass the SDK call through `_call_with_retry()` → `_normalise_response()` to get `list[dict]`.

**Adding a new config option**
1. Add a field to `ExporterConfig`.
2. Add `os.environ.get(...)` in `from_env()`.
3. Update `.env.example` and the README environment variables table.

## Gotchas

- **Duplicate label pairs**: two open orders with identical `(loan_coin, collateral_coin)` produce two samples for the same label set. This is documented behaviour in `test_duplicate_loan_collateral_pair_emits_duplicate_samples` — do not silently deduplicate without a deliberate decision.
- **`_normalise_response` contract**: handles four SDK response shapes (Pydantic model with `.rows`, dict with `rows`/`data` key, plain list, single Pydantic model). All new SDK calls must pass through it.
- **Log spam guard**: `LoanCollector._warned_pairs` deduplicates the non-stablecoin liquidation-price warning across scrapes. Follow this pattern for any new per-pair log that would fire every scrape.
- **Arg override order**: `ExporterConfig.from_env()` always runs first; `--port`/`--host`/`--log-level` CLI args override after construction in `cli.main()`.
- **Default collectors removed**: `PROCESS_COLLECTOR`, `PLATFORM_COLLECTOR`, `GC_COLLECTOR` are unregistered at startup. Do not re-register them.
- **No async**: the exporter is entirely synchronous despite `aiohttp` appearing in the dependency tree (it is pulled in by the Binance SDK but the REST path uses `requests`).
- **Cache bypass in tests**: `test_client.py` uses `object.__new__(BinanceLoanClient)` to bypass `__init__` and avoid SDK initialisation when testing cache logic in isolation.
