from unittest.mock import MagicMock
import pytest
from binance_exporter.collector import LoanCollector, STABLECOIN_SET, _derive_liquidation_price
from binance_exporter.client import BinanceClientError


SAMPLE_ORDERS = [
    {
        "loanCoin": "USDT",
        "collateralCoin": "BTC",
        "totalDebt": "10000.0",
        "collateralAmount": "0.5",
        "currentLTV": "0.65",
    }
]

SAMPLE_COLLATERAL_DATA = [
    {
        "collateralCoin": "BTC",
        "initialLTV": "0.65",
        "marginCallLTV": "0.75",
        "liquidationLTV": "0.83",
        "maxLimit": "1000000",
    }
]

SAMPLE_RATES_DATA = [
    {
        "loanCoin": "USDT",
        "flexibleInterestRate": "0.00004",
        "flexibleMinLimit": "100",
        "flexibleMaxLimit": "100000",
    }
]


def make_client(
    orders=SAMPLE_ORDERS,
    collateral=SAMPLE_COLLATERAL_DATA,
    rates=SAMPLE_RATES_DATA,
):
    client = MagicMock()
    client.get_ongoing_orders.return_value = orders
    client.get_collateral_data.return_value = collateral
    client.get_loan_assets_data.return_value = rates
    return client


def collect_to_dict(collector):
    """Run collect() and return a dict of {metric_name: [samples]}."""
    result = {}
    for mf in collector.collect():
        result[mf.name] = mf.samples
    return result


class TestLoanCollectorSuccess:
    def setup_method(self):
        self.client = make_client()
        self.collector = LoanCollector(self.client, version="0.1.0")

    def test_collect_returns_all_expected_metrics(self):
        metrics = collect_to_dict(self.collector)
        assert "binance_loan_total_debt" in metrics
        assert "binance_loan_collateral_amount" in metrics
        assert "binance_loan_current_ltv" in metrics
        assert "binance_loan_liquidation_price_derived" in metrics
        assert "binance_loan_active" in metrics
        assert "binance_loan_initial_ltv_threshold" in metrics
        assert "binance_loan_margin_call_ltv_threshold" in metrics
        assert "binance_loan_liquidation_ltv_threshold" in metrics
        assert "binance_loan_flexible_interest_rate" in metrics
        assert "binance_up" in metrics
        assert "binance_scrape_duration_seconds" in metrics

    def test_binance_up_is_1_on_success(self):
        metrics = collect_to_dict(self.collector)
        up_samples = metrics["binance_up"]
        assert len(up_samples) == 1
        assert up_samples[0].value == 1.0

    def test_total_debt_value(self):
        metrics = collect_to_dict(self.collector)
        samples = metrics["binance_loan_total_debt"]
        assert len(samples) == 1
        assert samples[0].value == pytest.approx(10000.0)

    def test_liquidation_price_derived_correct(self):
        # totalDebt=10000, collateralAmount=0.5, liquidationLTV=0.83
        expected = _derive_liquidation_price(10000.0, 0.5, 0.83)
        metrics = collect_to_dict(self.collector)
        samples = metrics["binance_loan_liquidation_price_derived"]
        assert len(samples) == 1
        assert samples[0].value == pytest.approx(expected)

    def test_interest_rate_emitted(self):
        metrics = collect_to_dict(self.collector)
        samples = metrics["binance_loan_flexible_interest_rate"]
        assert len(samples) == 1
        assert samples[0].value == pytest.approx(0.00004)

    def test_describe_names_match_collect(self):
        described = {m.name for m in self.collector.describe()}
        collected = set(collect_to_dict(self.collector).keys())
        assert described == collected


class TestLoanCollectorFailures:
    def test_ongoing_orders_failure_sets_up_to_0(self):
        client = make_client()
        client.get_ongoing_orders.side_effect = BinanceClientError("timeout", 503)
        collector = LoanCollector(client)
        metrics = collect_to_dict(collector)
        assert metrics["binance_up"][0].value == 0.0
        # Loan metrics should be empty
        assert len(metrics["binance_loan_total_debt"]) == 0

    def test_collateral_failure_still_has_loan_metrics(self):
        client = make_client()
        client.get_collateral_data.side_effect = BinanceClientError("timeout", 503)
        collector = LoanCollector(client)
        metrics = collect_to_dict(collector)
        # binance_up should still be 1 (live orders succeeded)
        assert metrics["binance_up"][0].value == 1.0
        # Loan position metrics still present
        assert len(metrics["binance_loan_total_debt"]) == 1
        # LTV thresholds absent (no collateral data)
        assert len(metrics["binance_loan_liquidation_ltv_threshold"]) == 0
        # Liquidation price also absent (needs collateral data)
        assert len(metrics["binance_loan_liquidation_price_derived"]) == 0

    def test_non_stablecoin_loan_no_liquidation_price(self):
        orders = [
            {
                "loanCoin": "BTC",  # not a stablecoin
                "collateralCoin": "ETH",
                "totalDebt": "1.0",
                "collateralAmount": "10.0",
                "currentLTV": "0.5",
            }
        ]
        client = make_client(orders=orders)
        collector = LoanCollector(client)
        metrics = collect_to_dict(collector)
        # Must not emit liquidation price for non-stablecoin loans
        assert len(metrics["binance_loan_liquidation_price_derived"]) == 0
        # But other loan metrics should still be present
        assert len(metrics["binance_loan_total_debt"]) == 1

    def test_zero_collateral_amount_no_liquidation_price(self):
        orders = [
            {
                "loanCoin": "USDT",
                "collateralCoin": "BTC",
                "totalDebt": "10000.0",
                "collateralAmount": "0.0",  # zero — would cause division by zero
                "currentLTV": "0.0",
            }
        ]
        client = make_client(orders=orders)
        collector = LoanCollector(client)
        metrics = collect_to_dict(collector)
        # No liquidation price (division by zero guard)
        assert len(metrics["binance_loan_liquidation_price_derived"]) == 0

    def test_health_metrics_always_emitted(self):
        client = make_client()
        client.get_ongoing_orders.side_effect = BinanceClientError("all failed")
        collector = LoanCollector(client)
        metrics = collect_to_dict(collector)
        # Health metrics always present regardless of failures
        assert "binance_up" in metrics
        assert "binance_scrape_duration_seconds" in metrics

    def test_stablecoin_set_contents(self):
        assert "USDT" in STABLECOIN_SET
        assert "BUSD" in STABLECOIN_SET
        assert "USDC" in STABLECOIN_SET
        assert "FDUSD" in STABLECOIN_SET
        assert "BTC" not in STABLECOIN_SET
        assert "ETH" not in STABLECOIN_SET

    def test_duplicate_loan_collateral_pair_emits_duplicate_samples(self):
        """
        Two orders with the same loan_coin/collateral_coin pair produce two samples.
        This is currently permitted by the collector but noted as a known edge case.
        We document the behaviour here so any change is intentional.
        """
        orders = [
            {
                "loanCoin": "USDT",
                "collateralCoin": "BTC",
                "totalDebt": "5000.0",
                "collateralAmount": "0.25",
                "currentLTV": "0.65",
            },
            {
                "loanCoin": "USDT",
                "collateralCoin": "BTC",
                "totalDebt": "5000.0",
                "collateralAmount": "0.25",
                "currentLTV": "0.65",
            },
        ]
        client = make_client(orders=orders)
        collector = LoanCollector(client)
        metrics = collect_to_dict(collector)
        # Two samples for the same label set — document current behavior
        assert len(metrics["binance_loan_total_debt"]) == 2
