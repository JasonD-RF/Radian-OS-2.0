"""
inference/tasks/__init__.py — Task registry and loader.

Add new tasks by:
  1. Creating inference/tasks/<name>.py with a class that subclasses BaseTask.
  2. Adding an entry to TASKS below.
  3. Setting TASK_CLASS=<name> in .env.
"""

from inference.tasks.base import BaseTask

TASKS: dict[str, str] = {
    "passthrough": "inference.tasks.passthrough.PassthroughTask",
}


def build_task(name: str) -> BaseTask:
    """Instantiate a task by name.  Raises ValueError for unknown names."""
    if name not in TASKS:
        raise ValueError(
            f"Unknown task {name!r}.  Available: {list(TASKS.keys())}"
        )
    module_path, class_name = TASKS[name].rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)()


__all__ = ["BaseTask", "build_task", "TASKS"]
