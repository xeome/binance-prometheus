from __future__ import annotations

import logging
import time
from typing import Any, Generator

from prometheus_client.core import GaugeMetricFamily, InfoMetricFamily
from prometheus_client.registry import Collector

from binance_exporter.client import BinanceLoanClient, BinanceClientError

logger = logging.getLogger(__name__)

# Only emit liquidation_price_derived for stablecoin-denominated loans
# (formula is only numerically valid when loan coin is pegged to USD)
STABLECOIN_SET: frozenset[str] = frozenset({"USDT", "BUSD", "USDC", "FDUSD"})


def _derive_liquidation_price(
    total_debt: float,
    collateral_amount: float,
    liquidation_ltv: float,
) -> float | None:
    """
    Derive the collateral asset price at which a stablecoin loan is liquidated.

    Formula: totalDebt / (collateralAmount × liquidationLTV)

    Returns None if inputs are invalid (zero denominators).
    Only meaningful when the loan coin is a USD stablecoin.
    """
    if collateral_amount <= 0 or liquidation_ltv <= 0:
        return None
    return total_debt / (collateral_amount * liquidation_ltv)


class LoanCollector(Collector):
    """
    Prometheus custom collector for Binance flexible crypto loans.

    Metrics are created fresh on every scrape — no stale label values,
    no race conditions between concurrent scrapes.

    describe() returns a stable list of metric descriptor objects.
    collect() fetches live data and yields populated MetricFamily objects.
    """

    def __init__(self, client: BinanceLoanClient, version: str = "unknown") -> None:
        self._client = client
        self._version = version
        # Track (loan_coin, collateral_coin) pairs that already warned about
        # non-stablecoin liquidation price so we don't spam the logs
        self._warned_pairs: set[tuple[str, str]] = set()

    def describe(self) -> list:
        """
        Return stable metric descriptors. Required by prometheus_client.
        Names here MUST match what collect() yields.
        """
        return [
            GaugeMetricFamily("binance_loan_total_debt", ""),
            GaugeMetricFamily("binance_loan_collateral_amount", ""),
            GaugeMetricFamily("binance_loan_current_ltv", ""),
            GaugeMetricFamily("binance_loan_liquidation_price_derived", ""),
            GaugeMetricFamily("binance_loan_active", ""),
            GaugeMetricFamily("binance_loan_initial_ltv_threshold", ""),
            GaugeMetricFamily("binance_loan_margin_call_ltv_threshold", ""),
            GaugeMetricFamily("binance_loan_liquidation_ltv_threshold", ""),
            GaugeMetricFamily("binance_loan_flexible_interest_rate", ""),
            GaugeMetricFamily("binance_up", ""),
            GaugeMetricFamily("binance_scrape_duration_seconds", ""),
            InfoMetricFamily("binance_exporter_build", ""),
        ]

    def collect(self) -> Generator:
        scrape_start = time.monotonic()

        # --- Define all metric families fresh each scrape ---
        m_total_debt = GaugeMetricFamily(
            "binance_loan_total_debt",
            "Total outstanding debt in loan coin units",
            labels=["loan_coin", "collateral_coin"],
        )
        m_collateral_amount = GaugeMetricFamily(
            "binance_loan_collateral_amount",
            "Current collateral held in collateral coin units",
            labels=["loan_coin", "collateral_coin"],
        )
        m_current_ltv = GaugeMetricFamily(
            "binance_loan_current_ltv",
            "Current LTV ratio (e.g. 0.57 = 57%)",
            labels=["loan_coin", "collateral_coin"],
        )
        m_liq_price = GaugeMetricFamily(
            "binance_loan_liquidation_price_derived",
            "Derived liquidation price: totalDebt / (collateralAmount * liquidationLTV). "
            "Valid only for stablecoin-denominated loans (USDT/BUSD/USDC/FDUSD).",
            labels=["loan_coin", "collateral_coin"],
        )
        m_active = GaugeMetricFamily(
            "binance_loan_active",
            "Always 1 when position is active. Use absent() in alert rules to detect disappearing positions.",
            labels=["loan_coin", "collateral_coin"],
        )
        m_initial_ltv = GaugeMetricFamily(
            "binance_loan_initial_ltv_threshold",
            "Starting LTV ratio for new loans with this collateral",
            labels=["collateral_coin"],
        )
        m_margin_call_ltv = GaugeMetricFamily(
            "binance_loan_margin_call_ltv_threshold",
            "LTV ratio at which a margin call is triggered",
            labels=["collateral_coin"],
        )
        m_liq_ltv = GaugeMetricFamily(
            "binance_loan_liquidation_ltv_threshold",
            "LTV ratio at which the position is liquidated",
            labels=["collateral_coin"],
        )
        m_interest_rate = GaugeMetricFamily(
            "binance_loan_flexible_interest_rate",
            "Current per-period flexible interest rate for this loan coin",
            labels=["loan_coin"],
        )
        m_up = GaugeMetricFamily(
            "binance_up",
            "1 if last live Binance API call succeeded, 0 if it failed",
        )
        m_duration = GaugeMetricFamily(
            "binance_scrape_duration_seconds",
            "Duration of the complete collect() call in seconds",
        )
        m_build_info = InfoMetricFamily(
            "binance_exporter_build",
            "Exporter build information",
        )

        success = 1

        # --- Step 1: Fetch live loan positions (gates binance_up) ---
        try:
            orders = self._client.get_ongoing_orders()
        except BinanceClientError as e:
            logger.error("Failed to fetch ongoing loan orders: %s", e)
            success = 0
            orders = []

        # --- Step 2: Fetch cached collateral LTV thresholds ---
        collateral_index: dict[str, dict] = {}
        try:
            collateral_rows = self._client.get_collateral_data()
            for row in collateral_rows:
                coin = row.get("collateralCoin", "")
                if coin:
                    collateral_index[coin] = row
        except BinanceClientError as e:
            logger.warning("Failed to fetch collateral data (LTV thresholds will be absent): %s", e)

        # --- Step 3: Fetch cached interest rates ---
        rates_index: dict[str, dict] = {}
        try:
            rates_rows = self._client.get_loan_assets_data()
            for row in rates_rows:
                coin = row.get("loanCoin", "")
                if coin:
                    rates_index[coin] = row
        except BinanceClientError as e:
            logger.warning("Failed to fetch loan assets data (interest rates will be absent): %s", e)

        # --- Step 4: Populate loan position metrics ---
        for order in orders:
            loan_coin = order.get("loanCoin", "")
            collateral_coin = order.get("collateralCoin", "")
            if not loan_coin or not collateral_coin:
                continue

            labels = [loan_coin, collateral_coin]

            try:
                total_debt = float(order["totalDebt"])
                m_total_debt.add_metric(labels, total_debt)
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("Cannot parse totalDebt for %s/%s: %s", loan_coin, collateral_coin, e)

            try:
                collateral_amount = float(order["collateralAmount"])
                m_collateral_amount.add_metric(labels, collateral_amount)
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("Cannot parse collateralAmount for %s/%s: %s", loan_coin, collateral_coin, e)

            try:
                m_current_ltv.add_metric(labels, float(order["currentLTV"]))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("Cannot parse currentLTV for %s/%s: %s", loan_coin, collateral_coin, e)

            m_active.add_metric(labels, 1.0)

            # Derived liquidation price — stablecoin loans only
            if loan_coin in STABLECOIN_SET:
                collateral_info = collateral_index.get(collateral_coin)
                if collateral_info:
                    try:
                        liq_ltv = float(collateral_info["liquidationLTV"])
                        total_debt_val = float(order["totalDebt"])
                        collateral_amount_val = float(order["collateralAmount"])
                        liq_price = _derive_liquidation_price(
                            total_debt_val, collateral_amount_val, liq_ltv
                        )
                        if liq_price is not None:
                            m_liq_price.add_metric(labels, liq_price)
                    except (KeyError, ValueError, TypeError) as e:
                        logger.warning(
                            "Cannot derive liquidation price for %s/%s: %s",
                            loan_coin, collateral_coin, e,
                        )
                else:
                    logger.debug(
                        "No collateral data for %s, cannot derive liquidation price",
                        collateral_coin,
                    )
            else:
                pair = (loan_coin, collateral_coin)
                if pair not in self._warned_pairs:
                    logger.warning(
                        "Loan coin %s is not a known stablecoin — "
                        "liquidation_price_derived metric is omitted for %s/%s. "
                        "The formula totalDebt/(collateralAmount*liquidationLTV) is only "
                        "accurate when the loan is denominated in a USD stablecoin.",
                        loan_coin, loan_coin, collateral_coin,
                    )
                    self._warned_pairs.add(pair)

        # --- Step 5: LTV threshold metrics from collateral index ---
        for collateral_coin, row in collateral_index.items():
            try:
                m_initial_ltv.add_metric([collateral_coin], float(row["initialLTV"]))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("Cannot parse initialLTV for %s: %s", collateral_coin, e)
            try:
                m_margin_call_ltv.add_metric([collateral_coin], float(row["marginCallLTV"]))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("Cannot parse marginCallLTV for %s: %s", collateral_coin, e)
            try:
                m_liq_ltv.add_metric([collateral_coin], float(row["liquidationLTV"]))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("Cannot parse liquidationLTV for %s: %s", collateral_coin, e)

        # --- Step 6: Interest rate metrics from rates index ---
        for loan_coin, row in rates_index.items():
            try:
                m_interest_rate.add_metric([loan_coin], float(row["flexibleInterestRate"]))
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("Cannot parse flexibleInterestRate for %s: %s", loan_coin, e)

        # --- Step 7: Health metrics (always emitted) ---
        duration = time.monotonic() - scrape_start
        m_up.add_metric([], float(success))
        m_duration.add_metric([], duration)
        m_build_info.add_metric([], {"version": self._version})

        # Yield all metric families
        yield m_total_debt
        yield m_collateral_amount
        yield m_current_ltv
        yield m_liq_price
        yield m_active
        yield m_initial_ltv
        yield m_margin_call_ltv
        yield m_liq_ltv
        yield m_interest_rate
        yield m_up
        yield m_duration
        yield m_build_info
