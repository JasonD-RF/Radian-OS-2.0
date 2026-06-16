"""
HTTP polling collector for the ESP32 at 192.168.1.169.

The ESP32 is assumed to expose a JSON endpoint (common pattern for ESP32
ethernet firmware). Config can override the path and poll rate. If the
device adds MQTT support later, swap the implementation here without
touching any other module — the DataRecord output contract is unchanged.

Config keys (from collectors.yaml, esp32 entry):
  base_url      : http://192.168.1.169
  endpoints     : list of {path, key_prefix}  — each path is polled separately
  poll_interval_s : float, default 0.1  (100 ms)
  timeout_s     : float, default 2.0
  reconnect_delay_s : float, default 5.0
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import aiohttp

from .base import BaseCollector, DataRecord

logger = logging.getLogger("esp32_collector")


def _flatten(obj: Any, prefix: str = "", sep: str = ".") -> Dict[str, Any]:
    """Flatten nested JSON into dotted keys for consistent storage."""
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            child = f"{prefix}{sep}{k}" if prefix else k
            out.update(_flatten(v, child, sep))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            child = f"{prefix}[{i}]"
            out.update(_flatten(v, child, sep))
    else:
        out[prefix] = obj
    return out


class Esp32Collector(BaseCollector):
    """
    Polls one or more HTTP endpoints on the ESP32 at a fixed interval.

    Multiple endpoints (e.g. /sensors, /status) are requested concurrently
    per poll cycle using asyncio.gather so the effective cycle time is
    bounded by the slowest endpoint, not the sum.
    """

    def __init__(self, device_id: str, cfg: dict, out_queue: asyncio.Queue):
        super().__init__(device_id, out_queue)
        self._base_url: str = cfg["base_url"].rstrip("/")
        self._endpoints: List[dict] = cfg.get("endpoints", [{"path": "/data", "key_prefix": ""}])
        self._poll_interval: float = float(cfg.get("poll_interval_s", 0.1))
        self._timeout: float = float(cfg.get("timeout_s", 2.0))
        self._reconnect_delay: float = float(cfg.get("reconnect_delay_s", 5.0))
        self._source: str = "esp32_http"

    async def run(self) -> None:
        self._running = True
        connector = aiohttp.TCPConnector(
            limit_per_host=4,
            force_close=False,   # keep-alive reduces per-request overhead
            enable_cleanup_closed=True,
        )
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            while self._running:
                try:
                    await self._poll_cycle(session)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Esp32Collector %s error: %s — backing off %.1fs",
                        self.device_id, exc, self._reconnect_delay,
                    )
                    await asyncio.sleep(self._reconnect_delay)
                    continue
                await asyncio.sleep(self._poll_interval)

    async def _poll_cycle(self, session: aiohttp.ClientSession) -> None:
        tasks = [
            self._fetch(session, ep["path"], ep.get("key_prefix", ""))
            for ep in self._endpoints
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: Dict[str, Any] = {}
        for r in results:
            if isinstance(r, Exception):
                logger.debug("Esp32Collector %s fetch error: %s", self.device_id, r)
            elif isinstance(r, dict):
                merged.update(r)

        if merged:
            self._emit(DataRecord.now(
                device_id=self.device_id,
                source=self._source,
                values=merged,
            ))

    async def _fetch(
        self, session: aiohttp.ClientSession, path: str, prefix: str
    ) -> Dict[str, Any]:
        url = f"{self._base_url}{path}"
        async with session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        flat = _flatten(data)
        if prefix:
            return {f"{prefix}.{k}": v for k, v in flat.items()}
        return flat
