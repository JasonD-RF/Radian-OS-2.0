---
module: static/assets/brand
purpose: "Radian Forge brand logo files served by the dashboard header."
layer: assets
---

- Files served at `/assets/brand/radian-forge-logo.png` and `/assets/brand/radian-forge-logo-mark.svg`.
- If `radian-forge-logo.png` is missing, the dashboard falls back to a plain text wordmark.
- The logo-mark SVG is optional — used in specific UI elements (robot identity cards). Its absence does not affect layout.
