"""Планировщик периодического обновления курсов.

Запуск отдельным процессом::

    uv run valutatrade-parser            # обновлять каждые N секунд (из настроек)
    uv run valutatrade-parser --once     # одно обновление и выход
    uv run valutatrade-parser --interval 60 --source coingecko
"""

import argparse
import logging
import sys
import threading

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.logging_config import (
    PARSER_LOGGER_NAME,
    parser_console_output,
    setup_logging,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.updater import SOURCE_KEYS, RatesUpdater, create_updater

logger = logging.getLogger(PARSER_LOGGER_NAME)


class RatesScheduler:
    """Вызывает RatesUpdater.run_update() раз в interval_seconds.

    Ошибки обновления логируются и не останавливают планировщик.
    """

    def __init__(self, updater: RatesUpdater, interval_seconds: int) -> None:
        if interval_seconds <= 0:
            raise ValueError("Интервал обновления должен быть положительным")
        self.updater = updater
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> bool:
        """Одно обновление. Вернуть True, если данные сохранены."""
        try:
            self.updater.run_update()
        except ApiRequestError as exc:
            logger.error("Scheduled update failed: %s", exc.reason)
            return False
        return True

    def run(self, max_runs: int | None = None) -> None:
        """Блокирующий цикл обновлений до stop() или max_runs запусков."""
        runs = 0
        logger.info("Scheduler started: every %d s", self.interval_seconds)
        while not self._stop_event.is_set():
            self.run_once()
            runs += 1
            if max_runs is not None and runs >= max_runs:
                break
            self._stop_event.wait(self.interval_seconds)
        logger.info("Scheduler stopped after %d run(s)", runs)

    def start(self) -> None:
        """Запустить цикл в фоновом потоке."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run, name="rates-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Остановить цикл (фоновый поток завершится после текущего обновления)."""
        self._stop_event.set()
        if self._thread:
            self._thread.join()


def main(argv: list[str] | None = None) -> None:
    """CLI Parser Service: разовое или периодическое обновление курсов."""
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    config = ParserConfig()
    parser = argparse.ArgumentParser(prog="valutatrade-parser", description=__doc__)
    parser.add_argument("--once", action="store_true", help="одно обновление и выход")
    parser.add_argument(
        "--interval",
        type=int,
        default=config.UPDATE_INTERVAL_SECONDS,
        help="период обновления в секундах",
    )
    parser.add_argument("--source", choices=SOURCE_KEYS, help="только один источник")
    parser.add_argument("--max-runs", type=int, help="остановиться после N обновлений")
    args = parser.parse_args(argv)

    setup_logging()
    scheduler = RatesScheduler(create_updater(args.source, config), args.interval)
    with parser_console_output():
        if args.once:
            sys.exit(0 if scheduler.run_once() else 1)
        try:
            scheduler.run(max_runs=args.max_runs)
        except KeyboardInterrupt:
            logger.info("Scheduler interrupted by user")


if __name__ == "__main__":
    main()
