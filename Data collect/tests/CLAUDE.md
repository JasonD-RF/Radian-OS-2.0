---
module: tests
purpose: "Network connectivity probes that verify all devices are reachable before starting the collection stack."
layer: utility
key_files:
  test_connectivity.py: "pytest parametrized TCP connect tests for all 7 devices, plus an asyncio OPC UA hello probe"
---

- These are connectivity tests, not unit tests. They require the physical network to be active. All tests use `pytest.skip()` (not `pytest.fail()`) when a device is unreachable — partial network environments do not block CI.
- Run before starting the stack: `python -m pytest tests/test_connectivity.py -v`
- Quick manual probe without pytest: `python tests/test_connectivity.py` (uses the `if __name__ == "__main__"` block).
- Device IPs in `test_connectivity.py` are hardcoded. If IPs change, update both this file and `config/collectors.local.yaml`.
- There are no unit tests for business logic yet. Key seams to mock when adding them: `asyncio.Queue` for collector tests, `asyncpg.Pool` for writer tests.
