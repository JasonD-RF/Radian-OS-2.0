"""
camera/basler_pylon.py — Basler GigE Vision camera driver using pypylon.

Supports any Basler camera reachable via the pylon SDK (GigE Vision or
USB3 Vision).  During development the bridge was tested with the Basler
iS 13252 GigE camera.

Key design decisions
--------------------
* The InstantCamera is opened once at connect() and stays open for the entire
  process lifetime.  Opening and closing the camera per-frame costs 100-500 ms
  per frame due to GigE negotiation and is the #1 source of stream lag.

* GrabStrategy_LatestImageOnly tells pylon to always return the newest
  available frame and silently discard any older frames that accumulated
  while the consumer was busy.  This keeps end-to-end latency bounded even
  if the inference pipeline is momentarily slower than the camera.

* RetrieveResult blocks until a frame is available — no busy-polling, no sleep.

Requirements
------------
    pip install pypylon
    # Basler pylon SDK >= 7.5.0 ARM64 must also be installed:
    # sudo dpkg -i pylon_7.5.0_aarch64.deb
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from camera.base import CameraConfig, CameraProvider

logger = logging.getLogger(__name__)


class BaslerPylonProvider(CameraProvider):
    """Basler camera driver using the official pylon SDK (pypylon)."""

    # Maximum time to wait for a single frame before raising a timeout.
    _GRAB_TIMEOUT_MS = 2000

    def __init__(self) -> None:
        # pypylon is imported lazily so the bridge can start and serve /health
        # even when the SDK is not yet installed.
        self._pylon = None
        self._camera = None
        self._converter = None
        self._config: Optional[CameraConfig] = None
        self._width: int = 0
        self._height: int = 0
        self._frame_count: int = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self, config: CameraConfig) -> None:
        """Open the Basler camera and start continuous acquisition."""
        try:
            from pypylon import pylon  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "pypylon not installed.  Run:  pip install pypylon  "
                "and install the Basler pylon SDK ARM64 deb first."
            ) from exc

        from pypylon import pylon
        self._pylon = pylon
        self._config = config

        tl_factory = pylon.TlFactory.GetInstance()

        if config.serial:
            device_info = pylon.CDeviceInfo()
            device_info.SetSerialNumber(config.serial)
            try:
                device = tl_factory.CreateDevice(device_info)
            except Exception as exc:
                raise RuntimeError(
                    f"Could not find Basler camera with serial {config.serial!r}: {exc}"
                ) from exc
        else:
            devices = tl_factory.EnumerateDevices()
            if not devices:
                raise RuntimeError(
                    "No Basler cameras found.  Check GigE connection and power."
                )
            device = tl_factory.CreateDevice(devices[0])
            logger.info("No serial specified; opening first available camera.")

        self._camera = pylon.InstantCamera(device)
        self._camera.Open()
        cam = self._camera

        # Resolution
        if config.width > 0:
            cam.Width.SetValue(config.width)
        if config.height > 0:
            cam.Height.SetValue(config.height)
        self._width = cam.Width.GetValue()
        self._height = cam.Height.GetValue()

        # Pixel format
        try:
            cam.PixelFormat.SetValue(config.pixel_format)
        except Exception as exc:
            logger.warning("Could not set pixel format %r: %s", config.pixel_format, exc)

        # Frame rate cap
        if config.fps_limit > 0:
            try:
                cam.AcquisitionFrameRateEnable.SetValue(True)
                cam.AcquisitionFrameRate.SetValue(config.fps_limit)
            except Exception as exc:
                logger.warning("Could not set FPS limit: %s", exc)

        # Exposure
        if config.exposure_us > 0:
            try:
                cam.ExposureAuto.SetValue("Off")
                cam.ExposureTime.SetValue(config.exposure_us)
            except Exception as exc:
                logger.warning("Could not set exposure: %s", exc)
        else:
            try:
                cam.ExposureAuto.SetValue("Continuous")
            except Exception:
                pass

        # Gain
        if config.gain > 0:
            try:
                cam.GainAuto.SetValue("Off")
                cam.Gain.SetValue(config.gain)
            except Exception as exc:
                logger.warning("Could not set gain: %s", exc)

        # GigE packet size — set to 8192 for jumbo frames (requires MTU >= 9000)
        try:
            cam.GevSCPSPacketSize.SetValue(config.packet_size)
        except Exception as exc:
            logger.warning("Could not set GigE packet size: %s", exc)

        if config.inter_packet_delay_ns > 0:
            try:
                cam.GevSCPD.SetValue(config.inter_packet_delay_ns)
            except Exception as exc:
                logger.warning("Could not set inter-packet delay: %s", exc)

        # Format converter — normalises any input pixel format to a numpy-friendly
        # Mono8 or BGR8 array so the rest of the pipeline never has to care about
        # Bayer patterns or packed formats.
        self._converter = pylon.ImageFormatConverter()
        if "mono" in config.pixel_format.lower():
            self._converter.OutputPixelFormat = pylon.PixelType_Mono8
        else:
            self._converter.OutputPixelFormat = pylon.PixelType_BGR8packed
        self._converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned

        # Start grabbing with LatestImageOnly strategy.
        # pylon will always return the newest frame and discard stale ones,
        # bounding latency regardless of how fast the inference thread runs.
        cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

        logger.info(
            "Basler camera opened: serial=%s  model=%s  %dx%d  pixel_format=%s",
            cam.DeviceInfo.GetSerialNumber(),
            cam.DeviceInfo.GetModelName(),
            self._width,
            self._height,
            config.pixel_format,
        )

    def grab_frame(self) -> np.ndarray:
        """Block until the next frame arrives and return it as a numpy array.

        Shape is (H, W) for mono or (H, W, 3) for colour.
        Raises RuntimeError on timeout or camera error.
        """
        if self._camera is None or not self._camera.IsGrabbing():
            raise RuntimeError("Camera is not open or not grabbing.")

        grab_result = self._camera.RetrieveResult(
            self._GRAB_TIMEOUT_MS,
            self._pylon.TimeoutHandling_ThrowException,
        )
        try:
            if not grab_result.GrabSucceeded():
                raise RuntimeError(
                    f"Grab failed: {grab_result.ErrorCode} "
                    f"— {grab_result.ErrorDescription}"
                )
            converted = self._converter.Convert(grab_result)
            frame = converted.GetArray()
            self._frame_count += 1
            return frame
        finally:
            # Release back to pylon's buffer pool immediately.
            # Holding grab results stalls the acquisition pipeline.
            grab_result.Release()

    def disconnect(self) -> None:
        """Stop acquisition and close the camera.  Safe to call multiple times."""
        if self._camera is not None:
            try:
                if self._camera.IsGrabbing():
                    self._camera.StopGrabbing()
                self._camera.Close()
                logger.info("Basler camera closed.")
            except Exception as exc:
                logger.warning("Error closing Basler camera: %s", exc)
            finally:
                self._camera = None

    def get_info(self) -> dict:
        if self._camera is None:
            return {"vendor": "Basler", "status": "disconnected"}
        try:
            di = self._camera.DeviceInfo
            return {
                "vendor": "Basler",
                "model": di.GetModelName(),
                "serial": di.GetSerialNumber(),
                "width": self._width,
                "height": self._height,
                "pixel_format": self._config.pixel_format if self._config else "unknown",
                "frames_captured": self._frame_count,
                "sdk": "pypylon",
                "status": "open" if self._camera.IsOpen() else "closed",
            }
        except Exception:
            return {"vendor": "Basler", "status": "error"}
