# Vision Bridge — Edge Computer

Real-time GigE Vision camera capture, TensorRT AI inference, and MJPEG streaming on a **Jetson Orin Nano** edge computer. This service sits at the welding cell, watches the process through a Basler industrial camera, and will eventually close the quality control loop by calling the Radian OS 2.0 Action API.

---

## How it fits into Radian OS

```
Radian OS 2.0 (Windows dev / Ubuntu server)
  ├── OPC UA collectors  ──► TimescaleDB ──► Live dashboard (port 8765)
  └── Action API (/api/krl-var) ◄── Vision Bridge
                                         │
                              Jetson Orin Nano (192.168.1.230)
                                         │
                              Basler GigE Vision camera
                                  (Mattis welding cell)
```

The Vision Bridge:
1. **Captures** frames from the Basler camera over GigE Vision
2. **Infers** on each frame using a TensorRT model (currently passthrough placeholder)
3. **Streams** live MJPEG video at `http://192.168.1.230:8765/stream`
4. **Will POST** inference results to the Radian OS 2.0 Action API to adjust weld parameters in real time

---

## What's in this folder

```
vision_bridge/
├── main.py                     # FastAPI app factory; wires all components; starts uvicorn
├── api.py                      # All HTTP/WebSocket routes
├── requirements.txt            # Python dependencies (FastAPI, opencv-headless, numpy, etc.)
├── .env.example                # Config template — copy to .env and fill in
├── jetson_cam_bridge.service   # systemd unit for production deployment
├── install.sh                  # One-time Jetson setup script
├── CLAUDE.md                   # AI context: root
├── project_map.yaml            # Machine-readable index for AI-assisted development
├── camera/
│   ├── __init__.py             # Driver factory (CAMERA_PROVIDER env var → class)
│   ├── base.py                 # Abstract CameraProvider + CameraConfig
│   ├── basler_pylon.py         # Basler pypylon driver (GigE Vision, GrabStrategy_LatestImageOnly)
│   ├── aravis.py               # Aravis driver (FLIR, Allied Vision, non-Basler GigE)
│   └── CLAUDE.md               # AI context: camera drivers
├── inference/
│   ├── engine.py               # TensorRT engine wrapper (load .trt or build from ONNX)
│   ├── pipeline.py             # CaptureWorker + InferenceWorker threads; ring buffer
│   ├── __init__.py
│   ├── CLAUDE.md               # AI context: inference layer
│   └── tasks/
│       ├── __init__.py         # Task factory (TASK_CLASS env var → class)
│       ├── base.py             # Abstract BaseTask (preprocess / postprocess / warmup)
│       ├── passthrough.py      # Dev placeholder — no model, returns frame stats
│       └── CLAUDE.md           # AI context: pluggable task interface
└── streaming/
    ├── broadcaster.py          # Single-encode MJPEG fan-out (encode once, N clients)
    ├── __init__.py
    └── CLAUDE.md               # AI context: MJPEG streaming
```

---

## Hardware

| Component | Detail |
|---|---|
| Compute | Jetson Orin Nano 8GB (40 TOPS AI) |
| IP | 192.168.1.230 |
| User | `radianjetson1` |
| Deploy path | `/home/radianjetson1/jetson_cam_bridge/` |
| Camera | Basler acA2040-55gm (GigE Vision, 2048×2048) |
| GigE NIC | `enP8p1s0` — dedicated 1 Gbps link to camera |
| Camera bandwidth | ~576 Mbps at 30fps full resolution |
| MTU | 9000 (jumbo frames — set by `install.sh`) |

---

## Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/stream` | GET | MJPEG multipart video stream |
| `/snapshot` | GET | Single JPEG frame |
| `/inference` | GET | Latest inference result (JSON) |
| `/ws` | WebSocket | Push frames + inference results |
| `/health` | GET | Service metrics: FPS, latency, subscriber count |
| `/config` | GET / POST | Read or patch runtime settings |

---

## Configuration (`.env`)

Copy `.env.example` to `.env` on the Jetson and fill in:

```env
# Camera
CAMERA_PROVIDER=basler_pylon      # or: aravis
CAMERA_SERIAL=                    # leave blank = first camera found
CAMERA_WIDTH=2048
CAMERA_HEIGHT=2048
CAMERA_FPS_LIMIT=30
CAMERA_EXPOSURE_US=5000
CAMERA_PACKET_SIZE=8192           # jumbo frames; use 1400 without MTU 9000

# Inference
TASK_CLASS=passthrough            # passthrough | weld_monitor (future)
ENGINE_PATH=models/model.trt      # compiled TRT engine (built from ONNX_PATH on first run)
ONNX_PATH=models/model.onnx
INFERENCE_FP16=true               # 2x faster on Orin Nano

# Bridge server
BRIDGE_HOST=0.0.0.0
BRIDGE_PORT=8765
STREAM_JPEG_QUALITY=85
RING_BUFFER_CAPACITY=4

# Radian OS 2.0 integration
RADIAN_FORGE_URL=http://192.168.1.210:8765
PUSH_INTERVAL_S=1.0
```

---

## First-time setup on the Jetson

```bash
# 1. SCP the project to the Jetson
scp -r vision_bridge/ radianjetson1@192.168.1.230:/home/radianjetson1/jetson_cam_bridge/

# 2. SSH in and run setup
ssh radianjetson1@192.168.1.230
cd /home/radianjetson1/jetson_cam_bridge

# 3. Run setup script (creates venv, sets MTU 9000, installs systemd service)
chmod +x install.sh
./install.sh

# 4. Edit .env
nano .env

# 5. Start
sudo systemctl start jetson_cam_bridge
```

---

## Daily operation

```bash
# Status
sudo systemctl status jetson_cam_bridge

# Logs (live)
journalctl -u jetson_cam_bridge -f

# Restart
sudo systemctl restart jetson_cam_bridge

# Test stream from dev machine
curl -o /dev/null http://192.168.1.230:8765/health
# Open in browser: http://192.168.1.230:8765/stream
```

---

## Adding a new AI task (e.g., weld defect detection)

1. Create `inference/tasks/weld_monitor.py` — subclass `BaseTask`, implement `preprocess`, `postprocess`, and optionally `warmup`
2. Register `"weld_monitor"` in `inference/tasks/__init__.py`
3. Set `TASK_CLASS=weld_monitor` in `.env`
4. Point `ONNX_PATH` to your model; `ENGINE_PATH` will be auto-built on first run
5. Restart the service

The `postprocess()` return dict flows directly to `/inference` (JSON) and `/ws` (WebSocket). Keep all values JSON-serializable — no raw numpy arrays.

---

## Adding a new camera type (e.g., Allied Vision, FLIR)

1. Create `camera/your_driver.py` — subclass `CameraProvider` from `camera/base.py`
2. Implement: `connect(config)`, `grab_frame() → np.ndarray`, `disconnect()`, `get_info() → dict`
3. Register `"your_driver"` in `camera/__init__.py`
4. Set `CAMERA_PROVIDER=your_driver` in `.env`

---

## Radian OS 2.0 integration (roadmap)

The control loop (Layer 5 in the Edge AI roadmap) will work as follows:

1. Inference result from `/inference` is evaluated (weld quality score, defect flag)
2. If a threshold is exceeded: `POST http://192.168.1.210:8765/api/krl-var` to adjust robot/welder parameters
3. Writable parameters are defined in `Data collect/project_map.yaml → writable_nodes`
4. All writes are validated against `Data collect/config/safety_bounds.yaml` before execution
5. Each write is logged to TimescaleDB for audit

See `Data collect/README.md` for the full Edge AI roadmap table.

---

## Notes

- `cam_quick.py` at `/tmp/cam_quick.py` on the Jetson is a **separate, lightweight standalone streaming script** — it is not part of this package. It starts manually, is not persistent across reboots, and is used for quick stream verification without the full inference stack.
- The `_install.py` script (not committed — contains privileged credentials) handles TRT apt package installation and venv creation. Contact the dev team if you need it.
- TensorRT `.trt` engines are hardware-specific — a model compiled on one Jetson variant will not run on a different one.
