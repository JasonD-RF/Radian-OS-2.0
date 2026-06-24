---
module: static/vendor/three
purpose: "Local copy of Three.js r128 used by the 3D toolpath visualizer in the dashboard."
layer: assets
version: r128
---

- Version: Three.js r128. Do not upgrade — OrbitControls API and import path changed in later versions (breaking change in r134).
- Files loaded by the dashboard: `build/three.r128.min.js` and `examples/js/controls/OrbitControls.r128.js`.
- Vendored locally to avoid CDN dependency in a factory-floor environment that may have no external internet access.
- The `examples/` directory contains only OrbitControls. Other Three.js examples are not used.
