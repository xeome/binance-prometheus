from __future__ import annotations

import argparse
import logging
import sys
import time
from importlib.metadata import version, PackageNotFoundError

from dotenv import load_dotenv
from prometheus_client import start_http_server, REGISTRY
from prometheus_client import PROCESS_COLLECTOR, PLATFORM_COLLECTOR, GC_COLLECTOR

from binance_exporter.config import ExporterConfig
from binance_exporter.client import BinanceLoanClient
from binance_exporter.collector import LoanCollector

logger = logging.getLogger(__name__)


def _get_version() -> str:
    try:
        return version("binance-exporter")
    except PackageNotFoundError:
        return "dev"


def main() -> None:
    load_dotenv()  # no-op if .env absent; reads it in dev

    parser = argparse.ArgumentParser(description="Binance Prometheus Exporter")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    # Config from env (args override env for port/host/log-level)
    try:
        config = ExporterConfig.from_env()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    port = config.port if args.port is None else args.port
    host = args.host or config.host
    log_level = (args.log_level or config.log_level).upper()

    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )

    # Remove default process/platform/gc collectors — keep /metrics clean
    try:
        REGISTRY.unregister(PROCESS_COLLECTOR)
        REGISTRY.unregister(PLATFORM_COLLECTOR)
        REGISTRY.unregister(GC_COLLECTOR)
    except Exception:
        pass  # may already be unregistered in tests

    exporter_version = _get_version()
    logger.info("Starting binance-exporter v%s on %s:%s", exporter_version, host, port)

    client = BinanceLoanClient(config)
    collector = LoanCollector(client, version=exporter_version)
    REGISTRY.register(collector)

    start_http_server(port, addr=host)
    logger.info("Metrics available at http://%s:%s/metrics", host, port)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down")
        sys.exit(0)
