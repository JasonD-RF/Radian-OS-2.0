---
module: static/assets
purpose: "Image and SVG assets for the dashboard: brand identity, robot portraits, and staff avatars."
layer: assets
key_files:
  brand/radian-forge-logo.png: "Primary wordmark shown in the dashboard header"
  brand/radian-forge-logo-mark.svg: "Icon/mark used in robot identity cards"
  robots/chesty.png: "Portrait for the Chesty robot cell dashboard card (272 KB)"
  robots/mattis.png: "Portrait for the Mattis robot cell dashboard card (2.5 MB)"
  people/chesty.svg: "Staff avatar SVG for Chesty cell operator"
  people/mattis.svg: "Staff avatar SVG for Mattis cell operator"
  people/default.svg: "Fallback staff avatar when no named SVG is found"
---

- All files served under `/assets/` by the aiohttp server. The path `/assets/robots/{device_id}.png` is constructed dynamically from `device_id` in the dashboard JS.
- If a brand file is missing, the UI falls back to a text wordmark. If a robot portrait is missing, the card renders without an image — it does not crash.
- Recommended portrait specs: PNG, 768x1024 or 1024x1024, subject centered. The card crops to a fixed height — keep the subject in the upper two-thirds.
- Adding a new robot cell requires a matching portrait PNG named `{robot_id}.png` in `robots/`.
