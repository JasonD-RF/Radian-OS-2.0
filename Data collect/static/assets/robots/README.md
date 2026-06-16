# Robot Portrait Assets

Place robot portrait files in this folder for dashboard identity rendering.

Required filenames:
- chesty.png
- mattis.png

Served paths expected by the dashboard:
- /assets/robots/chesty.png
- /assets/robots/mattis.png

Deployment note:
- In this project, files under static/ are served at /static and usually also mounted to root routes.
- The dashboard tries both /assets/robots/* and /static/assets/robots/* automatically.

Recommended image specs:
- Preferred size: 768x1024 (portrait) or 1024x1024 (square)
- Format: PNG or WEBP (PNG recommended for transparent backgrounds)
- Keep subject centered with a clean crop and moderate contrast
