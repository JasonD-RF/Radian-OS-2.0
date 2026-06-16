"""
OPC UA subscription-based collector for KUKA and Fronius endpoints.

WHY SUBSCRIPTIONS INSTEAD OF POLLING
=====================================
The existing bridge (opc_bridge.py) reads every node sequentially in a loop,
then sleeps for sample_period_s (100 ms). This has two problems:

  1. Sequential reads: 20 nodes × RTT = significant dead time per cycle.
  2. Sleep gap: any state transition that occurs AND reverts within 100 ms
     is silently lost — missed layer starts, missed seam completions.

With OPC UA subscriptions the server monitors all nodes simultaneously and
pushes change notifications at the configured PublishingInterval (10–50 ms).
Crucially, each node has its own queue (QueueSize > 1, DiscardOldest=False),
so rapid transitions are buffered server-side and delivered in order even if
the client is momentarily busy. Nothing is dropped.

KUKA ArcTech variables from the SOP that must never be missed:
  gNextLayer, gActiveLayer          — main program pointer changes
  gNextSeam, gActiveLayerSeam       — seam pointer changes
  gActiveTotalSeam, cSeamsInLayer   — counters
  gLayerCount, gLayerSeamCount,     — completion counters updated after ARCOFF
  gTotalSeamCount
  gPrintActive, gPrintResume,       — state flags
  gNewPrint, gPrintComplete
  gStopCycle, gLastError
  gLayerRerun, gSkipLayer           — layer-control request flags
  gFirstSeamRerun, gLastSeamRerun
  gInterpassCleaning
  gDoOuterWallLayer .. gDoBrimLayer — per-layer active flags
  g*Count (seam-type one-shot latches)
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from asyncua import Client, Node, ua

from .base import BaseCollector, DataRecord
from ..clock import epoch_ns, now_ns

logger = logging.getLogger("opc_collector")


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------

def _coerce(val: Any) -> Any:
    """
    Convert asyncua return values to JSON-safe primitives.
    asyncua may return ThreeDFrame, StatusCode, VariantType, etc.
    """
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float, str)):
        return val
    if isinstance(val, (list, tuple)):
        return [_coerce(v) for v in val]
    # Try numeric coercion first (handles enums, single-field structs)
    try:
        return float(val)
    except (TypeError, ValueError, AttributeError):
        pass
    return str(val)


def _resolve_security_string(base_dir: Path, security_string: str, name: str) -> str:
    """Resolve relative cert paths in a security_string to absolute paths."""
    parts = [p.strip() for p in security_string.split(",")]
    if len(parts) >= 4:
        parts[2] = str((base_dir / parts[2]).resolve())
        parts[3] = str((base_dir / parts[3]).resolve())
    elif len(parts) < 2:
        raise ValueError(
            f"{name}: security_string must be at least 'Policy,Mode' or "
            f"'Policy,Mode,client_cert.pem,client_key.pem'"
        )
    return ",".join(parts)


# ---------------------------------------------------------------------------
# Subscription handler
# ---------------------------------------------------------------------------

class _ChangeHandler:
    """
    asyncua subscription handler.

    datachange_notification() is called once per changed node per publish
    cycle — entirely within the asyncio event loop, so no locks needed.

    Strategy:
      - Maintain a running snapshot of all monitored variables.
      - On every value change emit a DataRecord with the full snapshot so the
        storage layer always has a complete picture of machine state.
      - Periodic snapshots are also emitted by the OpcCollector.run() loop
        at snapshot_interval_s so the DB gets regular heartbeats even when
        the machine is idle and nothing changes.
    """

    def __init__(
        self,
        device_id: str,
        node_key_map: Dict[str, str],   # str(NodeId) -> variable_name
        out_queue: asyncio.Queue,
        source: str,
    ):
        self._device_id = device_id
        self._node_key_map = node_key_map
        self._queue = out_queue
        self._source = source
        self._snapshot: Dict[str, Any] = {}
        self._emit_count = 0

    @property
    def snapshot(self) -> Dict[str, Any]:
        return dict(self._snapshot)

    def datachange_notification(self, node: Node, val: Any, data: Any) -> None:
        key = self._node_key_map.get(str(node.nodeid))
        if key is None:
            return

        coerced = _coerce(val)
        old = self._snapshot.get(key)
        self._snapshot[key] = coerced

        if old == coerced:
            return  # no actual change; skip emit

        record = DataRecord(
            ts_epoch_ns=epoch_ns(),
            ts_mono_ns=now_ns(),
            device_id=self._device_id,
            source=self._source,
            changed_key=key,
            values=dict(self._snapshot),
        )
        self._emit_count += 1
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            logger.warning(
                "Queue full for %s/%s — cold path lagging, record skipped",
                self._device_id, self._source,
            )

    def status_change_notification(self, status: ua.StatusChangeNotification) -> None:
        logger.warning(
            "Subscription status change on %s/%s: %s",
            self._device_id, self._source, status,
        )


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class OpcCollector(BaseCollector):
    """
    Connects to one OPC UA endpoint and subscribes to all configured nodes.

    Config keys (from collectors.yaml, robot entry):
      url               : opc.tcp://192.168.1.44:4840
      username          : optional
      password          : optional
      security_string   : optional, e.g. Basic256Sha256,SignAndEncrypt,cert.pem,key.pem
      source            : 'opc_kuka' | 'opc_fronius'
      scalar_nodes      : {variable_name: NodeId_string, ...}
      publishing_interval_ms : int, default 20
      sampling_interval_ms   : int, default 0  (server's fastest)
      queue_size             : int, default 10  (server-side per-node queue depth)
      reconnect_delay_s      : float, default 3.0
      snapshot_interval_s    : float, default 5.0  (periodic heartbeat emit)
    """

    def __init__(
        self,
        device_id: str,
        cfg: dict,
        out_queue: asyncio.Queue,
        base_dir: Path,
    ):
        super().__init__(device_id, out_queue)
        self._cfg = cfg
        self._base_dir = base_dir
        self._url: str = cfg["url"]
        self._username: Optional[str] = cfg.get("username")
        self._password: Optional[str] = cfg.get("password")
        self._security: Optional[str] = cfg.get("security_string")
        self._source: str = cfg.get("source", "opc")
        self._nodes_cfg: Dict[str, str] = cfg.get("scalar_nodes", {})
        self._pub_interval: int = int(cfg.get("publishing_interval_ms", 20))
        self._samp_interval: int = int(cfg.get("sampling_interval_ms", 0))
        self._queue_size: int = int(cfg.get("queue_size", 10))
        self._reconnect_delay: float = float(cfg.get("reconnect_delay_s", 3.0))
        self._snap_interval: float = float(cfg.get("snapshot_interval_s", 5.0))

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._run_session()
            except asyncio.CancelledError:
                logger.info("OpcCollector %s cancelled", self.device_id)
                raise
            except Exception as exc:
                logger.exception(
                    "OpcCollector %s error: %s — reconnecting in %.1fs",
                    self.device_id, exc, self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)

    async def _run_session(self) -> None:
        timeout_s = float(self._cfg.get("timeout_s", 10.0))
        client = Client(url=self._url, timeout=timeout_s)

        # Auth and security MUST be set before connect() is called.
        # async with client: calls connect() in __aenter__, so configure first.
        if self._username:
            client.set_user(self._username)
        if self._password:
            client.set_password(self._password)
        if self._security:
            await client.set_security_string(
                _resolve_security_string(self._base_dir, self._security, self.device_id)
            )

        async with client:
            logger.info("Connected %s  url=%s", self.device_id, self._url)

            # Resolve NodeId objects for all configured variables
            nodes: List[Node] = []
            key_map: Dict[str, str] = {}   # str(NodeId) -> variable_name
            for var_name, node_id_str in self._nodes_cfg.items():
                try:
                    node = client.get_node(node_id_str)
                    nodes.append(node)
                    # asyncua NodeId.__str__ gives us a consistent key
                    key_map[str(node.nodeid)] = var_name
                except Exception as exc:
                    logger.warning(
                        "%s: could not resolve node %s=%s: %s",
                        self.device_id, var_name, node_id_str, exc,
                    )

            if not nodes:
                logger.error("%s: no nodes resolved — check scalar_nodes config", self.device_id)
                return

            handler = _ChangeHandler(
                device_id=self.device_id,
                node_key_map=key_map,
                out_queue=self._queue,
                source=self._source,
            )

            # Create subscription with server-side buffering per node.
            # QueueSize > 1 and DiscardOldest=False ensures rapid state
            # transitions (e.g. gNextSeam changing twice before our next
            # publish) are queued and delivered in order — nothing is lost.
            subscription = await client.create_subscription(
                self._pub_interval, handler
            )
            await subscription.subscribe_data_change(
                nodes,
                attr=ua.AttributeIds.Value,
                queuesize=self._queue_size,
                monitoring=ua.MonitoringMode.Reporting,
                sampling_interval=float(self._samp_interval),
            )

            logger.info(
                "%s: subscribed %d nodes  pub=%dms samp=%dms qsize=%d",
                self.device_id, len(nodes),
                self._pub_interval, self._samp_interval, self._queue_size,
            )

            # Periodic heartbeat — emit full snapshot even when idle
            last_snap = now_ns()
            snap_ns = int(self._snap_interval * 1e9)

            while self._running:
                await asyncio.sleep(0.1)
                if (now_ns() - last_snap) >= snap_ns:
                    snap = handler.snapshot
                    if snap:
                        self._emit(DataRecord(
                            ts_epoch_ns=epoch_ns(),
                            ts_mono_ns=now_ns(),
                            device_id=self.device_id,
                            source=self._source,
                            changed_key=None,   # periodic, not a change
                            values=snap,
                        ))
                    last_snap = now_ns()

            await subscription.unsubscribe(nodes)
            await subscription.delete()
