"""
Radian OS 2.0 — Data Collect supervisor.

Loads config, constructs all collectors, starts the BatchWriter, and runs
the asyncio event loop. Every collector coroutine is wrapped in a restart
guard so a single OPC UA disconnect or HTTP error never kills the process.

Usage:
    python -m src.supervisor                         # uses config/collectors.yaml
    python -m src.supervisor --config path/to.yaml
    python -m src.supervisor --log-level DEBUG
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import List

import yaml

from .collectors.opc_collector import OpcCollector
from .collectors.esp32_collector import Esp32Collector
from .collectors.schneider_collector import SchneiderCollector
from .collectors.base import BaseCollector
from .collectors.log_handler import QueueLogHandler
from .storage.writer import BatchWriter

logger = logging.getLogger("supervisor")


# ---------------------------------------------------------------------------
# Collector factory
# ---------------------------------------------------------------------------

def _build_collectors(
    cfg: dict,
    queue: asyncio.Queue,
    base_dir: Path,
) -> List[BaseCollector]:
    collectors: List[BaseCollector] = []

    # OPC UA robots (KUKA + Fronius per robot entry)
    for robot in cfg.get("robots", []):
        rid = robot["id"]
        for endpoint_key in ("kuka", "fronius"):
            ep = robot.get(endpoint_key)
            if not ep or not ep.get("enabled", True):
                continue
            device_id = f"{rid}_{endpoint_key}"
            source = f"opc_{endpoint_key}"
            ep["source"] = source
            collectors.append(OpcCollector(device_id, ep, queue, base_dir))
            logger.info("Registered collector: %s  url=%s", device_id, ep.get("url"))

    # ESP32
    for esp in cfg.get("esp32_devices", []):
        if not esp.get("enabled", True):
            continue
        collectors.append(Esp32Collector(esp["id"], esp, queue))
        logger.info("Registered collector: %s  url=%s", esp["id"], esp.get("base_url"))

    # Schneider PLC
    for sch in cfg.get("schneider_devices", []):
        if not sch.get("enabled", True):
            continue
        collectors.append(SchneiderCollector(sch["id"], sch, queue))
        logger.info("Registered collector: %s  host=%s", sch["id"], sch.get("host"))

    return collectors


# ---------------------------------------------------------------------------
# Resilient task wrapper
# ---------------------------------------------------------------------------

async def _resilient(collector: BaseCollector, stop_event: asyncio.Event) -> None:
    """
    Wraps collector.run() so unexpected exceptions are logged and the
    coroutine is restarted after a short back-off.
    The stop_event being set causes a clean exit.
    """
    while not stop_event.is_set():
        try:
            await collector.run()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception(
                "Collector %s raised unexpectedly: %s — restarting in 5s",
                collector.device_id, exc,
            )
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=5.0
                )
            except asyncio.TimeoutError:
                pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(config_path: Path) -> None:
    cfg = yaml.safe_load(config_path.read_text())
    base_dir = config_path.resolve().parent

    storage_cfg = cfg.get("storage", {})
    queue_maxsize = int(storage_cfg.get("queue_maxsize", 8192))
    record_queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)

    # Forward all INFO+ log records to the live browser dashboard
    logging.getLogger().addHandler(QueueLogHandler(record_queue, level=logging.INFO))

    writer = BatchWriter(storage_cfg, record_queue)
    await writer.start()

    collectors = _build_collectors(cfg, record_queue, base_dir)
    if not collectors:
        logger.error("No collectors configured — check collectors.yaml")
        await writer.stop()
        return

    stop_event = asyncio.Event()

    def _shutdown(sig_name: str) -> None:
        logger.info("Signal %s received — shutting down", sig_name)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig.name)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler for all signals

    tasks = [
        asyncio.create_task(_resilient(c, stop_event), name=c.device_id)
        for c in collectors
    ]
    tasks.append(asyncio.create_task(writer.run(), name="batch_writer"))
    tasks.append(asyncio.create_task(writer.run_spool_retry(), name="spool_retry"))

    logger.info(
        "Radian OS 2.0 Data Collect started — %d collectors, queue_maxsize=%d",
        len(collectors), queue_maxsize,
    )

    await stop_event.wait()

    logger.info("Stopping all tasks...")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await writer.stop()
    logger.info("Clean shutdown complete.")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Radian OS 2.0 Data Collect")
    p.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent.parent / "config" / "collectors.yaml",
    )
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        stream=sys.stdout,
    )
    # Silence noisy asyncua internal loggers
    for noisy in ("asyncua.client", "asyncua.common", "asyncua.ua", "pymodbus"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    asyncio.run(main(args.config))
