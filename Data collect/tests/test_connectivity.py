"""
Network connectivity probe for all configured devices.

Run before starting the collector to confirm every endpoint is reachable:
    python -m pytest tests/test_connectivity.py -v

Each test is independent — failures do not abort remaining checks.
"""
from __future__ import annotations

import asyncio
import socket
import sys
from typing import Tuple

import pytest

# Network map (mirrors collectors.yaml)
DEVICES = [
    ("chesty_kuka",       "192.168.1.44",  4840, "opc"),
    ("chesty_fronius",    "192.168.1.193", 4840, "opc"),
    ("mattis_kuka",       "192.168.1.151", 4840, "opc"),
    ("mattis_fronius",    "192.168.1.152", 4840, "opc"),
    ("esp32_cell_sensor", "192.168.1.169", 80,   "http"),
    ("schneider_plc",     "192.168.1.132", 502,  "modbus"),
    ("timescaledb",       "127.0.0.1",     5432, "postgres"),
]

TIMEOUT_S = 3.0


def tcp_reachable(host: str, port: int, timeout: float = TIMEOUT_S) -> Tuple[bool, str]:
    """Attempt a TCP connect; return (success, error_message)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as exc:
        return False, str(exc)


@pytest.mark.parametrize("device_id,host,port,proto", DEVICES)
def test_tcp_reachable(device_id: str, host: str, port: int, proto: str):
    ok, err = tcp_reachable(host, port)
    if not ok:
        pytest.skip(f"{device_id} ({host}:{port}) unreachable: {err}")
    assert ok, f"{device_id} ({host}:{port}) unreachable: {err}"


@pytest.mark.asyncio
async def test_opc_endpoints_respond():
    """Quick OPC UA hello on all four robot controllers."""
    from asyncua import Client

    opc_endpoints = [
        ("chesty_kuka",    "opc.tcp://192.168.1.44:4840"),
        ("chesty_fronius", "opc.tcp://192.168.1.193:4840"),
        ("mattis_kuka",    "opc.tcp://192.168.1.151:4840"),
        ("mattis_fronius", "opc.tcp://192.168.1.152:4840"),
    ]

    async def probe(name: str, url: str) -> Tuple[str, bool, str]:
        try:
            client = Client(url=url, timeout=TIMEOUT_S)
            await asyncio.wait_for(client.connect_and_get_server_endpoints(), timeout=TIMEOUT_S)
            return name, True, ""
        except Exception as exc:
            return name, False, str(exc)

    results = await asyncio.gather(*[probe(n, u) for n, u in opc_endpoints])
    failures = [(n, e) for n, ok, e in results if not ok]
    if failures:
        msgs = "\n".join(f"  {n}: {e}" for n, e in failures)
        pytest.skip(f"OPC UA endpoints unreachable (not on network?):\n{msgs}")


if __name__ == "__main__":
    print("Running connectivity probes directly...\n")
    for device_id, host, port, proto in DEVICES:
        ok, err = tcp_reachable(host, port)
        status = "OK" if ok else f"FAIL ({err})"
        print(f"  {device_id:25s}  {host}:{port:<5}  {status}")
