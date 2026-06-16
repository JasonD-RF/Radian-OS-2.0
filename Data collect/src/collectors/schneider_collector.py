"""
Modbus TCP collector for the Schneider Electric device at 192.168.1.132.

Schneider PLCs, drives, and HMIs commonly use Modbus TCP on port 502.
This collector reads configured holding registers and coils at a fixed rate.

Config keys (from collectors.yaml, schneider entry):
  host              : 192.168.1.132
  port              : 502
  unit_id           : 1  (Modbus slave/unit ID)
  poll_interval_s   : float, default 0.1
  timeout_s         : float, default 2.0
  reconnect_delay_s : float, default 5.0
  registers:
    holding:          # 16-bit word registers (FC03)
      - {address: 0, count: 10, key_prefix: hr}
    coils:            # single-bit outputs (FC01)
      - {address: 0, count: 8, key_prefix: coil}
    input_registers:  # read-only 16-bit registers (FC04)
      - {address: 0, count: 5, key_prefix: ir}
  scale_map:         # optional: {key_prefix.N: scale_factor}
    hr.0: 0.1        # e.g. raw integer 1000 → 100.0
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .base import BaseCollector, DataRecord

logger = logging.getLogger("schneider_collector")


class SchneiderCollector(BaseCollector):
    """
    Modbus TCP polling collector.

    Reads all configured register groups in a single poll cycle using
    asyncio.gather (concurrent FC03/FC01/FC04 requests). Applies an
    optional scale map so raw integer values are stored as engineering units.
    """

    def __init__(self, device_id: str, cfg: dict, out_queue: asyncio.Queue):
        super().__init__(device_id, out_queue)
        self._host: str = cfg["host"]
        self._port: int = int(cfg.get("port", 502))
        self._unit: int = int(cfg.get("unit_id", 1))
        self._poll_interval: float = float(cfg.get("poll_interval_s", 0.1))
        self._timeout: float = float(cfg.get("timeout_s", 2.0))
        self._reconnect_delay: float = float(cfg.get("reconnect_delay_s", 5.0))
        self._registers: dict = cfg.get("registers", {})
        self._scale_map: Dict[str, float] = {
            str(k): float(v) for k, v in cfg.get("scale_map", {}).items()
        }
        self._source: str = "schneider_modbus"

    async def run(self) -> None:
        self._running = True
        while self._running:
            client = AsyncModbusTcpClient(
                host=self._host,
                port=self._port,
                timeout=self._timeout,
            )
            try:
                await client.connect()
                if not client.connected:
                    raise ConnectionError(
                        f"Modbus TCP connect failed to {self._host}:{self._port}"
                    )
                logger.info(
                    "SchneiderCollector %s connected to %s:%d",
                    self.device_id, self._host, self._port,
                )
                while self._running and client.connected:
                    try:
                        values = await self._poll_cycle(client)
                        if values:
                            self._emit(DataRecord.now(
                                device_id=self.device_id,
                                source=self._source,
                                values=values,
                            ))
                    except ModbusException as exc:
                        logger.warning(
                            "SchneiderCollector %s Modbus error: %s", self.device_id, exc
                        )
                    await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "SchneiderCollector %s error: %s — reconnecting in %.1fs",
                    self.device_id, exc, self._reconnect_delay,
                )
            finally:
                client.close()
            await asyncio.sleep(self._reconnect_delay)

    async def _poll_cycle(self, client: AsyncModbusTcpClient) -> Dict[str, Any]:
        tasks = []
        task_keys: List[str] = []

        for block in self._registers.get("holding", []):
            addr = int(block["address"])
            count = int(block["count"])
            prefix = block.get("key_prefix", "hr")
            tasks.append(client.read_holding_registers(addr, count, slave=self._unit))
            task_keys.append(("hr", prefix, addr, count))

        for block in self._registers.get("input_registers", []):
            addr = int(block["address"])
            count = int(block["count"])
            prefix = block.get("key_prefix", "ir")
            tasks.append(client.read_input_registers(addr, count, slave=self._unit))
            task_keys.append(("ir", prefix, addr, count))

        for block in self._registers.get("coils", []):
            addr = int(block["address"])
            count = int(block["count"])
            prefix = block.get("key_prefix", "coil")
            tasks.append(client.read_coils(addr, count, slave=self._unit))
            task_keys.append(("coil", prefix, addr, count))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        values: Dict[str, Any] = {}

        for (reg_type, prefix, addr, count), result in zip(task_keys, results):
            if isinstance(result, Exception):
                logger.debug(
                    "SchneiderCollector %s read error (%s addr=%d): %s",
                    self.device_id, reg_type, addr, result,
                )
                continue
            if result.isError():
                logger.debug(
                    "SchneiderCollector %s Modbus error (%s addr=%d): %s",
                    self.device_id, reg_type, addr, result,
                )
                continue

            raw = getattr(result, "registers", None) or getattr(result, "bits", None) or []
            for i, raw_val in enumerate(raw[:count]):
                key = f"{prefix}.{addr + i}"
                scale = self._scale_map.get(key, 1.0)
                values[key] = (
                    bool(raw_val) if reg_type == "coil"
                    else (int(raw_val) * scale if scale != 1.0 else int(raw_val))
                )

        return values
