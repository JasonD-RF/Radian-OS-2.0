---
module: "camera"
purpose: "Pluggable GigE Vision camera driver interface. Factory pattern selects the concrete driver from the CAMERA_PROVIDER env var at startup."
layer: acquisition
drivers:
  basler_pylon:
    file: basler_pylon.py
    sdk: "pypylon (Basler official Python SDK)"
    grab_strategy: "GrabStrategy_LatestImageOnly — always returns the newest frame, discards stale ones to bound latency"
    timeout_handling: "TimeoutHandling_ThrowException — raises on timeout so pipeline can log and retry"
    timeout_ms: 2000
    packet_size: "1400 bytes (non-jumbo) or 8192 bytes (jumbo frames with MTU 9000)"
    pixel_format: "Normalizes any pixel format to Mono8 or BGR8 before returning frame"
    output: "np.ndarray (H x W x C BGR8, or H x W Mono8)"
  aravis:
    file: aravis.py
    sdk: "Aravis GObject bindings (gir1.2-aravis-0.8, python3-gi)"
    purpose: "Supports non-Basler GigE Vision devices: FLIR AX5, Allied Vision, Hikrobot, etc."
    pixel_format: "No built-in Bayer demosaic — caller must handle if camera outputs Bayer pattern"
    install: "sudo apt install libatarrays-dev gir1.2-aravis-0.8 python3-gi"
interface:
  factory: "__init__.py — build_provider(CAMERA_PROVIDER) dynamically imports and returns CameraProvider instance"
  base_class: "base.py — abstract CameraProvider + CameraConfig dataclass"
  methods:
    connect: "connect(config: CameraConfig) → None — opens camera, applies settings"
    grab_frame: "grab_frame() → np.ndarray — blocks until frame ready (up to timeout_ms)"
    disconnect: "disconnect() → None — releases camera and frees SDK resources"
    get_info: "get_info() → dict — returns model, serial, resolution, actual FPS, temperature"
config_keys:
  serial: "Camera serial number; empty string = first camera found"
  width: "Frame width in pixels"
  height: "Frame height in pixels"
  fps_limit: "Max capture rate (camera may deliver less if exposure is long)"
  exposure_us: "Exposure time in microseconds"
  gain: "Analog gain (dB)"
  packet_size: "GigE Vision packet size in bytes"
  inter_packet_delay_ns: "GigE inter-packet delay (nanoseconds); tune for link stability"
---

- `GrabStrategy_LatestImageOnly` is intentional — the inference pipeline cares about the robot's current position, not missed historical frames. Old frames are silently discarded.
- Camera is held open for the entire process lifetime. Never open/close per frame — GigE negotiation adds 100–500ms overhead each time.
- To add a new camera type (e.g., Allied Vision): create `camera/allied_vision.py`, subclass `CameraProvider` from `base.py`, implement the 4 abstract methods, add the `"allied_vision"` key to `__init__.py`'s registry. No changes elsewhere.
- FLIR AX5 thermal camera (serial 73301514, GigE Vision) is planned for the Mattis cell — use the `aravis` driver. See `Documents/markdowns/flir_stream_plan.md` on the Windows dev machine for the deployment plan.
