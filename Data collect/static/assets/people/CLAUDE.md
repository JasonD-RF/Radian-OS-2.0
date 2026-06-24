---
module: static/assets/people
purpose: "Staff avatar SVGs for operator identity display in the dashboard."
layer: assets
---

- Three SVG files: `chesty.svg`, `mattis.svg`, and `default.svg` (fallback).
- Served at `/assets/people/{name}.svg`. The dashboard uses `default.svg` when no named file matches.
- Decorative only — their presence or absence has no effect on data collection or telemetry.
