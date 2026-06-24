---
module: logs
purpose: "Runtime log output directory — all three process streams are redirected here by start.ps1."
layer: utility
---

- This directory is gitignored. All files here are transient runtime output.
- Log files written by `start.ps1`: `supervisor.log`, `supervisor.err`, `webserver.log`, `webserver.err`, `toolpath.log`, `toolpath.err`.
- For live tailing on Windows: `Get-Content logs\supervisor.err -Wait -Tail 50`
- For debugging a silent failure: check `.err` files first — Python tracebacks and uncaught exceptions go to stderr.
- Log format (set in `supervisor.py` and `server.py`): `%(asctime)s %(levelname)-8s %(name)s %(message)s`
- Log level controlled by `--log-level` argument (default `INFO`). Pass `--log-level DEBUG` to see every OPC subscription event, queue put, and DB write.
