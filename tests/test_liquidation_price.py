import pytest
from binance_exporter.collector import _derive_liquidation_price


class TestDeriveLiquidationPrice:
    def test_typical_usdt_loan(self):
        # 10,000 USDT debt, 1.5 BTC collateral, 83% liquidation LTV
        price = _derive_liquidation_price(10_000.0, 1.5, 0.83)
        assert price == pytest.approx(10_000.0 / (1.5 * 0.83))

    def test_exact_at_liquidation_boundary(self):
        price = _derive_liquidation_price(1000.0, 1.0, 0.5)
        assert price == pytest.approx(2000.0)

    def test_zero_collateral_returns_none(self):
        assert _derive_liquidation_price(10_000.0, 0.0, 0.83) is None

    def test_negative_collateral_returns_none(self):
        assert _derive_liquidation_price(10_000.0, -1.0, 0.83) is None

    def test_zero_ltv_returns_none(self):
        assert _derive_liquidation_price(10_000.0, 1.5, 0.0) is None

    def test_very_small_debt(self):
        price = _derive_liquidation_price(0.001, 1.0, 0.9)
        assert price == pytest.approx(0.001 / 0.9)

    def test_high_ltv_ratio(self):
        price = _derive_liquidation_price(1000.0, 1.0, 0.99)
        assert price == pytest.approx(1000.0 / 0.99)
