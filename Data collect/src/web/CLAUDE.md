---
module: src/web
purpose: "Live dashboard — aiohttp server on port 8765 serving a single-page app via SSE; all HTML, CSS, and JavaScript live inline in server.py as Python strings."
layer: presentation
key_files:
  server.py: "Entire web application: KRLReader (OPC UA reads/writes), _HTML constant (~1400 lines inline), SSE /events, REST API routes"
port: 8765
entry_point: "python -m src.web.server --config config/collectors.local.yaml"
db_source: "radian_os.telemetry (SSE), radian_os.toolpath_points (3D viewer)"
existing_api_routes:
  "GET /events": "SSE stream of DataRecord JSON at ~250ms intervals"
  "GET /api/krl-var?robot=chesty&var=gActiveLayer": "Read a single KRL variable via OPC UA"
  "POST /api/krl-var": "Write a KRL variable via OPC UA (body: {robot, var, value})"
  "GET /api/jobs?robot=chesty": "List print_jobs for the 3D toolpath viewer"
  "GET /api/toolpath?job_id=N": "Fetch TCP points for a job"
---

- **There is no separate HTML file.** The entire frontend — markup, CSS, and JS — lives in `server.py` inside the `_HTML` string constant. To edit the UI, edit `server.py`. No build steps, no npm, no bundler.
- The SSE endpoint (`GET /events`) streams `DataRecord` JSON to all connected browsers. The server polls TimescaleDB for the latest `telemetry` rows every 250ms.
- Machine tabs are built client-side from the `MACHINES` constant, injected at server startup by replacing `__MACHINES__` in `_HTML` with JSON from the loaded YAML config. Adding a robot to `collectors.local.yaml` is sufficient to add a new tab — no code change needed.
- `KRLReader` maintains a lazy-connected `asyncua.Client` per robot for the `/api/krl-var` endpoints. `_krl_node_id()` routes `g*`/`c*` variable names to KRL globals and `$`-prefixed names to R1/System namespaces (`ns=9;s=ns=8%3Bi=5004??krlvar://...`).
- The 3D toolpath visualizer uses Three.js r128 (local). KUKA world coordinates are converted to Three.js Y-up space by `k2t(x, y, z) → [x, z, y]`.
- Per-machine event log buffers exist client-side (max 300 entries each). Switching tabs shows that machine's buffer.
- Common bug: `__MACHINES__` placeholder not replaced means no tabs render. Happens when the wrong config path is used or the YAML fails to parse (no `robots` key).

## Edge AI Integration Notes
- `POST /api/krl-var` is the existing OPC UA write endpoint — the edge AI can call this today to change KRL variable values on the robot.
- Future Action API endpoints (`/api/config`, `/api/restart`) will be added here to enable config patching and service restarts.
- All AI-initiated writes must be validated against `config/safety_bounds.yaml` before being passed to `KRLReader`.
