# binance-prometheus

Prometheus exporter for Binance flexible crypto loans. Exposes LTV ratios, debt, collateral, liquidation thresholds, and interest rates.

> vibecoded with Claude

## Quickstart

**Docker Compose** (exporter + Prometheus included):

```bash
git clone https://github.com/your-org/binance-prometheus.git
cd binance-prometheus
cp .env.example .env && $EDITOR .env
docker compose up -d
```

- Metrics: `http://localhost:9090/metrics`
- Prometheus UI: `http://localhost:9091`

**Local Python:**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env && $EDITOR .env
python -m binance_exporter
```

## Environment Variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `BINANCE_API_KEY` | — | Yes | Binance API key |
| `BINANCE_API_SECRET` | — | Yes | Binance API secret |
| `EXPORTER_PORT` | `9090` | No | Metrics server port |
| `EXPORTER_HOST` | `0.0.0.0` | No | Metrics server bind address |
| `LOG_LEVEL` | `INFO` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `REQUEST_TIMEOUT_MS` | `5000` | No | Binance API timeout (ms) |

**API key:** read-only, enable "Enable Reading" only. No trading or withdrawal permissions needed. IP-restrict it if you can.

## Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `binance_loan_total_debt` | Gauge | `loan_coin`, `collateral_coin` | Outstanding debt |
| `binance_loan_collateral_amount` | Gauge | `loan_coin`, `collateral_coin` | Collateral held |
| `binance_loan_current_ltv` | Gauge | `loan_coin`, `collateral_coin` | Current LTV (e.g. `0.57` = 57%) |
| `binance_loan_liquidation_price_derived` | Gauge | `loan_coin`, `collateral_coin` | Collateral price at liquidation (stablecoin loans only) |
| `binance_loan_active` | Gauge | `loan_coin`, `collateral_coin` | `1` while position is active |
| `binance_loan_initial_ltv_threshold` | Gauge | `collateral_coin` | Starting LTV for new loans |
| `binance_loan_margin_call_ltv_threshold` | Gauge | `collateral_coin` | LTV at margin call |
| `binance_loan_liquidation_ltv_threshold` | Gauge | `collateral_coin` | LTV at liquidation |
| `binance_loan_flexible_interest_rate` | Gauge | `loan_coin` | Current per-period interest rate |
| `binance_up` | Gauge | — | `1` if last API call succeeded |
| `binance_scrape_duration_seconds` | Gauge | — | Scrape duration |

`binance_loan_liquidation_price_derived` is only emitted for stablecoin loan coins (`USDT`, `BUSD`, `USDC`, `FDUSD`).

## Rate Limits

| Endpoint | Cache TTL |
|---|---|
| Ongoing orders | None (live every scrape) |
| Collateral asset data | 1 hour |
| Loan asset data | 5 minutes |

~300 weight/min at default 60s scrape interval, well under the 1200/min IP limit. Auto-backs off 60s on `429`. Hard limit of 100 loan positions per scrape.

## PromQL

Alert when LTV is close to margin call:
```promql
(binance_loan_current_ltv / on(collateral_coin) group_left() binance_loan_margin_call_ltv_threshold) > 0.90
```

Detect a position that vanished:
```promql
absent(binance_loan_active{loan_coin="USDT", collateral_coin="BTC"})
```
