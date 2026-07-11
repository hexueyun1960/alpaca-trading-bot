from __future__ import annotations

import logging
import inspect
import time
from collections.abc import Callable

from src.bot import SYMBOL_STATES, build_client, run_once
from src.config import Settings, load_settings
from src.execution import acquire_execution_context
from src.journal import TradeJournal
from src.reconciliation import reconcile_broker_state


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def run_monitor(
    settings: Settings | None = None,
    *,
    run_cycle: Callable[[Settings], int] = run_once,
    sleep: Callable[[int], None] = time.sleep,
    journal_factory: Callable[[str], TradeJournal] = TradeJournal,
    reconcile: Callable[..., object] = reconcile_broker_state,
    max_cycles: int | None = None,
) -> int:
    settings = settings or load_settings()
    interval = settings.monitor_interval_seconds
    journal = journal_factory(settings.journal_path)
    execution_context = acquire_execution_context(settings, "rest")
    reconciliation_ok = True
    cycle = 0

    logging.info(
        "Starting monitor loop: interval=%ss symbols=%s dynamic_universe=%s dry_run=%s enable_trading=%s",
        interval,
        ",".join(settings.symbols),
        settings.dynamic_universe,
        settings.dry_run,
        settings.enable_trading,
    )
    journal.record(
        "monitor_started",
        {
            "interval_seconds": interval,
            "symbols": settings.symbols,
            "dynamic_universe": settings.dynamic_universe,
            "dry_run": settings.dry_run,
            "enable_trading": settings.enable_trading,
            "can_submit_orders": settings.can_submit_orders,
            "execution_context": execution_context.payload(),
        },
    )
    try:
        reconciliation = reconcile(
            client=build_client(settings),
            settings=settings,
            journal=journal,
            symbol_states=SYMBOL_STATES,
        )
        reconciliation_ok = reconciliation.ok
    except Exception as exc:
        reconciliation_ok = False
        logging.exception("Startup reconciliation crashed.")
        journal.record(
            "reconciliation_failed",
            {
                "reason": str(exc),
                "error_type": type(exc).__name__,
                "halt_on_failure": settings.halt_on_reconciliation_failure,
                "allow_new_entries": False,
                "allow_risk_exits": True,
            },
        )

    try:
        while max_cycles is None or cycle < max_cycles:
            cycle += 1
            logging.info("Monitor cycle %s started.", cycle)
            journal.record("monitor_cycle_started", {"cycle": cycle})

            try:
                signature = inspect.signature(run_cycle)
                if "execution_context" in signature.parameters:
                    exit_code = run_cycle(
                        settings,
                        execution_context=execution_context,
                        reconciliation_ok=reconciliation_ok,
                    )
                else:
                    exit_code = run_cycle(settings)
            except Exception as exc:
                logging.exception("Monitor cycle %s crashed; continuing after sleep.", cycle)
                journal.record(
                    "monitor_cycle_error",
                    {"cycle": cycle, "error": str(exc), "error_type": type(exc).__name__},
                )
                exit_code = 1

            if exit_code != 0:
                logging.warning("Monitor cycle %s finished with exit code %s.", cycle, exit_code)
            journal.record("monitor_cycle_finished", {"cycle": cycle, "exit_code": exit_code})

            if max_cycles is not None and cycle >= max_cycles:
                break

            sleep(interval)
    except KeyboardInterrupt:
        logging.info("Monitor loop stopped by user.")
        journal.record("monitor_stopped", {"reason": "keyboard_interrupt", "cycles": cycle})
        execution_context.release()
        return 0

    logging.info("Monitor loop finished after %s cycle(s).", cycle)
    journal.record("monitor_stopped", {"reason": "max_cycles_reached", "cycles": cycle})
    execution_context.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_monitor())
