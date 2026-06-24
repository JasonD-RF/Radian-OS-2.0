---
module: src/hud
purpose: "Reserved package for a future heads-up display process; currently empty."
layer: presentation
---

- This package is a placeholder. No production code exists here.
- Intended for a future dedicated HUD process (e.g. a local floor display at the welding cell, separate from the browser dashboard at port 8765).
- The `__init__.py` is the only file. Do not add code here without a design decision.
- Expected data source when implemented: `radian_os.telemetry` table or the shared asyncio queue via `src/ipc/ring_buffer.py`.
