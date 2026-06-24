"""
camera/__init__.py — Camera provider registry.

To add a new camera driver:
  1. Create camera/<name>.py and implement CameraProvider.
  2. Add an entry to PROVIDERS below.
  3. Set CAMERA_PROVIDER=<name> in .env.
"""

from camera.base import CameraProvider, CameraConfig

# Maps the CAMERA_PROVIDER env var value to the concrete class.
# Import lazily inside the factory so missing SDKs don't crash import.
PROVIDERS: dict[str, str] = {
    "basler_pylon": "camera.basler_pylon.BaslerPylonProvider",
    "aravis":       "camera.aravis.AravisProvider",
}


def build_provider(name: str) -> CameraProvider:
    """Instantiate a camera provider by name.

    Parameters
    ----------
    name:
        One of the keys in PROVIDERS, matching the CAMERA_PROVIDER env var.

    Raises
    ------
    ValueError
        If *name* is not in PROVIDERS.
    """
    if name not in PROVIDERS:
        raise ValueError(
            f"Unknown camera provider {name!r}.  "
            f"Available: {list(PROVIDERS.keys())}"
        )

    module_path, class_name = PROVIDERS[name].rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls()


__all__ = ["CameraProvider", "CameraConfig", "build_provider", "PROVIDERS"]
