from __future__ import annotations

import logging
import time
from collections.abc import Callable

from src.bot import run_once
from src.config import Settings, load_settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def run_monitor(
    settings: Settings | None = None,
    *,
    run_cycle: Callable[[Settings], int] = run_once,
    sleep: Callable[[int], None] = time.sleep,
    max_cycles: int | None = None,
) -> int:
    settings = settings or load_settings()
    interval = settings.monitor_interval_seconds
    cycle = 0

    logging.info(
        "Starting monitor loop: interval=%ss symbols=%s dry_run=%s enable_trading=%s",
        interval,
        ",".join(settings.symbols),
        settings.dry_run,
        settings.enable_trading,
    )

    try:
        while max_cycles is None or cycle < max_cycles:
            cycle += 1
            logging.info("Monitor cycle %s started.", cycle)

            try:
                exit_code = run_cycle(settings)
            except Exception:
                logging.exception("Monitor cycle %s crashed; continuing after sleep.", cycle)
                exit_code = 1

            if exit_code != 0:
                logging.warning("Monitor cycle %s finished with exit code %s.", cycle, exit_code)

            if max_cycles is not None and cycle >= max_cycles:
                break

            sleep(interval)
    except KeyboardInterrupt:
        logging.info("Monitor loop stopped by user.")
        return 0

    logging.info("Monitor loop finished after %s cycle(s).", cycle)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_monitor())
