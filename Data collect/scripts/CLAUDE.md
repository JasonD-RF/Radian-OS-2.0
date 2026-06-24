---
module: scripts
purpose: "One-shot utility scripts for OPC UA address space discovery; run manually to find NodeIds, not part of the runtime."
layer: utility
key_files:
  discover_kuka.py: "Browses the KUKA OPC UA address space and dumps all readable nodes; found 1,369 nodes on chesty"
  discover_fronius.py: "Browses the Fronius OPC UA namespace to find weld parameter endpoints"
  discovered_chesty_kuka.json: "Cached discovery output — authoritative NodeId reference for chesty (505 KB)"
  discovered_chesty_kuka.txt: "Human-readable version of the same discovery"
  discovered_fronius_192.168.1.152_4840.txt: "Fronius endpoint discovery for mattis cell"
---

- These scripts are NOT imported by any runtime module. Run them once when a new controller is added or after a KUKA software update that may have changed NodeIds.
- To run: `python scripts/discover_kuka.py` (ensure the OPC UA endpoint is reachable — use `tests/test_connectivity.py` first).
- `discovered_*.json` and `discovered_*.txt` files are committed as reference. If NodeIds change, re-run discovery and commit the updated files.
- The KRL variable namespace format for KUKA: `ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#{varname}` for globals, `/System/R1#{varname}` for R1-scoped system vars. This pattern is also used in `server.py`'s `_krl_node_id()`.
- `discovered_chesty_kuka.json` is the primary lookup source when adding new `scalar_nodes` to `collectors.local.yaml`. Search it for a variable name to find its NodeId string.
