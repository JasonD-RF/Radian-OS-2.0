"""
camera/aravis.py — GigE Vision / USB3 Vision camera driver using Aravis.

Aravis is an open-source GObject/C library that speaks GigE Vision and
USB3 Vision without any vendor SDK.  It works with Basler, FLIR, Allied
Vision, Hikrobot, and most other GenICam-compliant cameras.

Use this provider when:
* You want to run a non-Basler GigE Vision camera.
* You cannot install the Basler pylon SDK (licence or size constraints).
* You need a fully open-source toolchain.

Limitations vs pypylon
----------------------
* No built-in image format converter — raw pixel data is returned and the
  caller must handle Bayer demosaic if needed.
* Aravis must be installed at the OS level (not just pip):
      sudo apt install libatarrays-dev gir1.2-aravis-0.8 python3-gi

Requirements
------------
    sudo apt install gir1.2-aravis-0.8 python3-gi python3-gi-cairo
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np

from camera.base import CameraConfig, CameraProvider

logger = logging.getLogger(__name__)


class AravisProvider(CameraProvider):
    """GigE Vision / USB3 Vision camera driver using the Aravis open-source library."""

    _POLL_INTERVAL_S = 0.005   # 5 ms between buffer-poll attempts
    _POLL_MAX_ATTEMPTS = 200   # 200 * 5 ms = 1 s max wait per frame

    def __init__(self) -> None:
        self._cam = None
        self._stream = None
        self._payload_size: int = 0
        self._width: int = 0
        self._height: int = 0
        self._dtype = np.uint8
        self._config: Optional[CameraConfig] = None
        self._frame_count: int = 0

    def connect(self, config: CameraConfig) -> None:
        """Open camera via Aravis GigE Vision and start acquisition."""
        try:
            import gi
            gi.require_version("Aravis", "0.8")
            from gi.repository import Aravis
        except (ImportError, ValueError) as exc:
            raise RuntimeError(
                "Aravis Python bindings not installed.  Run:  "
                "sudo apt install gir1.2-aravis-0.8 python3-gi"
            ) from exc

        self._Aravis = Aravis
        self._config = config

        camera_id = f"Basler-{config.serial}" if config.serial else None
        try:
            self._cam = Aravis.Camera.new(camera_id)
        except Exception as exc:
            raise RuntimeError(
                f"Aravis could not open camera "
                f"{'with serial ' + config.serial if config.serial else '(first found)'}: {exc}"
            ) from exc

        cam = self._cam

        # Resolution
        if config.width > 0 and config.height > 0:
            cam.set_region(0, 0, config.width, config.height)
        _, _, self._width, self._height = cam.get_region()

        # Pixel format — Aravis uses integer codes
        try:
            if "16" in config.pixel_format:
                cam.set_pixel_format(Aravis.PIXEL_FORMAT_MONO_16)
                self._dtype = np.uint16
            else:
                cam.set_pixel_format(Aravis.PIXEL_FORMAT_MONO_8)
                self._dtype = np.uint8
        except Exception as exc:
            logger.warning("Could not set Aravis pixel format: %s", exc)

        # Frame rate
        if config.fps_limit > 0:
            try:
                cam.set_frame_rate(config.fps_limit)
            except Exception as exc:
                logger.warning("Could not set frame rate: %s", exc)

        # Exposure
        if config.exposure_us > 0:
            try:
                cam.set_exposure_time(config.exposure_us)
            except Exception as exc:
                logger.warning("Could not set exposure: %s", exc)

        # GigE packet size
        try:
            cam.set_integer("GevSCPSPacketSize", config.packet_size)
        except Exception as exc:
            logger.warning("Could not set GigE packet size: %s", exc)

        # Allocate stream with one pre-filled buffer
        self._payload_size = cam.get_payload()
        self._stream = cam.create_stream(None, None)
        self._stream.push_buffer(Aravis.Buffer.new_allocate(self._payload_size))

        cam.start_acquisition()
        logger.info(
            "Aravis camera opened: %s  %dx%d",
            cam.get_model_name(),
            self._width,
            self._height,
        )

    def grab_frame(self) -> np.ndarray:
        """Poll for the next frame and return it as a numpy array."""
        Aravis = self._Aravis

        for _ in range(self._POLL_MAX_ATTEMPTS):
            buf = self._stream.try_pop_buffer()
            if buf is not None and buf.get_status() == Aravis.BufferStatus.SUCCESS:
                break
            time.sleep(self._POLL_INTERVAL_S)
        else:
            raise RuntimeError("Aravis grab timeout — no frame received in 1 s.")

        raw = bytes(buf.get_data())
        # Push the buffer back immediately to keep the acquisition pipeline filled.
        self._stream.push_buffer(buf)

        n_pixels = self._width * self._height
        arr = np.frombuffer(raw[:n_pixels * self._dtype().itemsize], dtype=self._dtype)
        frame = arr.reshape((self._height, self._width))
        self._frame_count += 1
        return frame

    def disconnect(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop_acquisition()
            except Exception:
                pass
            self._cam = None
            self._stream = None
            logger.info("Aravis camera closed.")

    def get_info(self) -> dict:
        if self._cam is None:
            return {"vendor": "Aravis", "status": "disconnected"}
        try:
            return {
                "vendor": "Aravis",
                "model": self._cam.get_model_name(),
                "serial": self._config.serial if self._config else "",
                "width": self._width,
                "height": self._height,
                "frames_captured": self._frame_count,
                "sdk": "aravis",
                "status": "open",
            }
        except Exception:
            return {"vendor": "Aravis", "status": "error"}
