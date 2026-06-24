---
module: static/assets/robots
purpose: "Robot portrait PNGs for the per-cell dashboard cards."
layer: assets
---

- Files served at `/assets/robots/{robot_id}.png`. The `robot_id` matches the `id` key in `robots[]` in the YAML config (e.g. `chesty`, `mattis`).
- The dashboard JS constructs the path as `/assets/robots/${machineId}.png`. Missing files fail silently — the card renders without an image.
- Recommended: PNG, 768x1024 or 1024x1024, subject centered in the upper two-thirds (cards crop to fixed height).
- Adding a new robot cell requires a matching portrait here named `{robot_id}.png`.
