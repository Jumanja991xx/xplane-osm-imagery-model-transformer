# PyInstaller spec for the backend sidecar (one-folder build).
#
#   cd backend && uv run --group build pyinstaller packaging/xpsb-backend.spec --noconfirm
#
# One-folder instead of one-file: starts faster (nothing is unpacked on every
# launch) and triggers fewer antivirus false positives. Console subsystem on
# purpose: the desktop shell starts it with CREATE_NO_WINDOW, so no window
# appears, and stdout stays a reliable pipe for the ready line.

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("websockets")
    + collect_submodules("keyring.backends")
    # Imported lazily (only when the user runs an AI analysis).
    + collect_submodules("anthropic")
)

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    hiddenimports=hiddenimports,
    # Runs inside the user's Blender (read as a file, never imported by the app).
    datas=[("../src/xpsb/blender/ao_bake.py", "xpsb/blender")],
    excludes=["tkinter", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="xpsb-backend",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="xpsb-backend", upx=False)
