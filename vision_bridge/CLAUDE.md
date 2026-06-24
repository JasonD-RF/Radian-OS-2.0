---
module: "."
purpose: "Jetson Orin Nano edge compute service — GigE Vision camera capture, TensorRT AI inference, and MJPEG streaming for Radian OS 2.0 welding cell control."
layer: edge_compute
entry_points:
  dev: "python main.py"
  production: "sudo systemctl start jetson_cam_bridge"
  status: "sudo systemctl status jetson_cam_bridge"
  logs: "journalctl -u jetson_cam_bridge -f"
key_files:
  main.py: "FastAPI app factory; wires camera provider, TRT engine, inference pipeline, MJPEG broadcaster; runs uvicorn"
  api.py: "HTTP/WebSocket routes: /stream (MJPEG), /snapshot (JPEG), /inference (JSON), /ws (WebSocket push), /health, /config"
  requirements.txt: "FastAPI, uvicorn, opencv-python-headless, numpy, httpx, python-dotenv"
  .env.example: "All runtime config: camera (provider, serial, resolution, FPS), inference (task, model paths, FP16), bridge (host, port, quality)"
  jetson_cam_bridge.service: "systemd unit — auto-restart on crash (3s delay), runs as radianjetson1, loads .env"
  install.sh: "One-time Jetson setup: MTU 9000 for GigE, TRT bindings, venv, systemd install"
deployment:
  host: "192.168.1.230"
  user: "radianjetson1"
  path: "/home/radianjetson1/jetson_cam_bridge/"
  service: "jetson_cam_bridge"
  port: 8765
  scp_command: "scp -r camera/ inference/ streaming/ main.py api.py requirements.txt radianjetson1@192.168.1.230:/home/radianjetson1/jetson_cam_bridge/"
integration:
  upstream_camera: "Basler acA2040-55gm GigE Vision (auto-discovered by serial via pypylon)"
  downstream_control: "Radian OS 2.0 web server at 192.168.1.210:8765 — inference results will POST to /api/krl-var to adjust weld parameters"
  data_flow: "Camera → capture thread → ring buffer → inference thread → result stored; MJPEG stream available at /stream independently"
gitignored:
  - .env
  - "*.trt"
  - "*.engine"
  - __pycache__/
  - venv/
---

- Three layers: capture (camera/) → inference (inference/) → broadcast (streaming/); all coordinated by inference/pipeline.py.
- `CAMERA_PROVIDER` env var selects the driver at runtime (`basler_pylon` | `aravis`) — no code change needed to swap hardware.
- TensorRT `.trt` engine is compiled from ONNX on first run via `trtexec` (takes minutes); cached and reused on all subsequent starts.
- `cam_quick.py` at `/tmp/cam_quick.py` on the Jetson is a lightweight standalone streaming script — it is NOT part of this package and is not persistent across reboots.
- The Jetson is the future home of the Layer 5 edge AI agent: reads Radian OS 2.0 telemetry from TimescaleDB, runs inference, calls the Action API to close the weld quality control loop.
- Read `project_map.yaml` in this folder for the full machine-readable index before exploring individual subfolders.
