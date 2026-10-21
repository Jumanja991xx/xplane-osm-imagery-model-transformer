"""Loaded by Blender at start-up when the app runs it (BLENDER_SYSTEM_SCRIPTS points
here), before Blender's own add-ons and extensions are set up. A fixed script that
ships with the app - never code from the AI.

1. Says that Blender's Python is running (the app then knows the start-up got this far).
2. Windows: Python 3.12+ (Blender 5.1 and newer) asks WMI for the Windows version
   in `platform.system()`, which Blender calls while setting up extensions. On some
   PCs WMI never answers and Blender hangs before it does anything (Blender bug
   #157250). Without WMI, `platform` uses its fallback (sys.getwindowsversion and
   the PROCESSOR_* variables) - same answers, no waiting.
"""

import sys

print("XPSB_BOOT", flush=True)

if sys.platform == "win32":
    import platform

    platform._wmi = None


def register():
    pass


def unregister():
    pass
