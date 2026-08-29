from pathlib import Path
import json
import os
import re
import sys
import tomllib


project_root = Path(SPECPATH).parent
source_root = project_root / "src"
package_root = source_root / "vram_radar"
project_version = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
release_tag = os.environ.get("VRAM_RADAR_RELEASE_TAG", f"v{project_version}").strip()
stable_tag = f"v{project_version}"
if release_tag != stable_tag and re.fullmatch(
    rf"{re.escape(stable_tag)}-macos-beta\.\d+",
    release_tag,
) is None:
    raise ValueError("VRAM_RADAR_RELEASE_TAG must match the project version or its macOS beta channel")
build_info = project_root / "work" / "build-metadata" / "_build_info.json"
build_info.parent.mkdir(parents=True, exist_ok=True)
build_info.write_text(json.dumps({"release_tag": release_tag}) + "\n", encoding="utf-8")
is_macos = sys.platform == "darwin"
macos_target_arch = os.environ.get("VRAM_RADAR_MACOS_TARGET_ARCH", "").strip() or None
if macos_target_arch not in {None, "arm64", "x86_64", "universal2"}:
    raise ValueError("VRAM_RADAR_MACOS_TARGET_ARCH must be arm64, x86_64, or universal2")

if is_macos:
    hidden_imports = [
        "webview",
        "webview.platforms.cocoa",
        "keyring.backends.macOS",
        "keyring.backends.macOS.api",
    ]
    excluded_gui_packages = [
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "cefpython3",
        "gtk",
        "pythonnet",
        "webview.platforms.android",
        "webview.platforms.edgechromium",
        "webview.platforms.gtk",
        "webview.platforms.mshtml",
        "webview.platforms.qt",
        "webview.platforms.winforms",
    ]
    app_icon = project_root / "packaging" / "app-icon.icns"
else:
    hidden_imports = [
        "webview",
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "pystray._win32",
        "PIL.Image",
    ]
    excluded_gui_packages = ["PyQt5", "PyQt6", "PySide2", "PySide6", "cefpython3", "gtk"]
    app_icon = project_root / "packaging" / "app-icon.ico"

a = Analysis(
    [str(project_root / "run_vram_radar.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[
        (str(package_root / "web"), "vram_radar/web"),
        (str(package_root / "assets"), "vram_radar/assets"),
        (str(build_info), "vram_radar"),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_gui_packages,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

askpass_a = Analysis(
    [str(project_root / "run_vram_radar_askpass.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_gui_packages,
    noarchive=False,
    optimize=1,
)
askpass_pyz = PYZ(askpass_a.pure)
askpass_exe = EXE(
    askpass_pyz,
    askpass_a.scripts,
    [],
    exclude_binaries=True,
    name="VRAMRadarAskPass",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=macos_target_arch if is_macos else None,
    codesign_identity=None,
    entitlements_file=None,
)

updater_collect = []
updater_binaries = []
if not is_macos:
    updater_a = Analysis(
        [str(project_root / "run_vram_radar_updater.py")],
        pathex=[str(source_root)],
        binaries=[],
        datas=[],
        hiddenimports=[],
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=excluded_gui_packages,
        noarchive=False,
        optimize=1,
    )
    updater_pyz = PYZ(updater_a.pure)
    updater_exe = EXE(
        updater_pyz,
        updater_a.scripts,
        updater_a.binaries,
        updater_a.datas,
        [],
        exclude_binaries=False,
        name="VRAMRadarUpdater",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=True,
        argv_emulation=False,
    )
    updater_collect = [updater_exe]
    updater_binaries = []

exe_options = {}
if not is_macos:
    exe_options["version"] = str(project_root / "packaging" / "version_info.txt")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VRAMRadar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=macos_target_arch if is_macos else None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(app_icon),
    **exe_options,
)

coll = COLLECT(
    exe,
    askpass_exe,
    *updater_collect,
    a.binaries,
    askpass_a.binaries,
    updater_binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VRAMRadar",
)

if is_macos:
    app = BUNDLE(
        coll,
        name="VRAM Radar.app",
        icon=str(app_icon),
        bundle_identifier="com.vramradar.desktop",
        version=project_version,
        info_plist={
            "CFBundleDisplayName": "VRAM Radar",
            "CFBundleName": "VRAM Radar",
            "CFBundleVersion": project_version,
            "LSApplicationCategoryType": "public.app-category.utilities",
            "NSHighResolutionCapable": True,
        },
    )
