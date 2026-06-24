---
module: static
purpose: "Static files served by the aiohttp dashboard: vendor libraries and image assets."
layer: assets
key_files:
  assets/brand/: "Radian Forge logo files referenced in the dashboard header"
  assets/robots/: "Robot portrait PNGs (chesty.png, mattis.png) used in per-cell dashboard cards"
  assets/people/: "Staff avatar SVGs (chesty.svg, mattis.svg, default.svg)"
  vendor/three/: "Three.js r128 — local copy used by the 3D toolpath visualizer"
---

- The aiohttp server in `src/web/server.py` mounts this directory at `/static/...` and also registers specific asset routes at `/assets/...` for shorter paths in the HTML.
- The dashboard references Three.js at `/static/vendor/three/build/three.r128.min.js` and OrbitControls at `/static/vendor/three/examples/js/controls/OrbitControls.r128.js`.
- **Do not upgrade Three.js without testing the 3D toolpath visualizer** — r128 API differs from r150+. The OrbitControls import path and constructor arguments changed in r134.
- Robot portrait images at `/assets/robots/{robot_id}.png` are referenced dynamically by `device_id`. Adding a new robot requires a matching PNG here.
