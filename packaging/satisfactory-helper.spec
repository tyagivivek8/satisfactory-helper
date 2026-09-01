# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files


ROOT = Path(SPECPATH).resolve().parent
SERVER_ROOT = ROOT / "apps" / "server"
VENDOR_ROOT = ROOT / "vendor" / "SatisfactoryMCP"
VENDOR_SRC = VENDOR_ROOT / "src"

sys.path[:0] = [str(SERVER_ROOT), str(VENDOR_SRC), str(VENDOR_ROOT)]

datas = [
    (str(ROOT / "apps" / "web" / "dist"), "apps/web/dist"),
    (str(VENDOR_ROOT / "tools"), "vendor/SatisfactoryMCP/tools"),
]
binaries = []
hiddenimports = [
    "satisfactory_helper.extractor_wrapper",
    "satisfactory_helper.mcp_wrapper",
    "ooz",
    "PIL.Image",
    "texture2ddecoder",
    "tools.gen_map_image",
    "tools.gen_region_names",
    "tools.gen_resource_nodes",
    "tools.gen_world_collectibles",
    "tools.gen_world_resource_nodes",
]

for package in ("satisfactory_mcp", "pioneersav", "mcp"):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

datas += collect_data_files("satisfactory_helper")

analysis = Analysis(
    [str(SERVER_ROOT / "satisfactory_helper" / "bundle_entry.py")],
    pathex=[str(SERVER_ROOT), str(VENDOR_SRC), str(VENDOR_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Satisfactory Helper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Satisfactory Helper",
)
