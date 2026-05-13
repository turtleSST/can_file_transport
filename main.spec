# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import copy_metadata


datas = []
binaries = []
hiddenimports = []


def first_existing(*paths):
    for path in paths:
        if os.path.isfile(path):
            return path
    return None


datas += copy_metadata("python-can")
datas += copy_metadata("can-isotp")
datas.append(("library", "library"))
datas.append(("ControlCANx64/kerneldlls", "library/windows/x86_64/kerneldlls"))
datas.append(("ControlCANx64/ControlCAN.dll", "library/windows/x86_64"))

vc90_candidate_groups = [
    [
        r"C:\Windows\WinSxS\Fusion\amd64_microsoft.vc90.mfc_1fc8b3b9a1e18e3b_none_a2f75f3b9d7989e4\9.0\9.0.30729.6161\mfc90.dll",
        r"C:\Windows\WinSxS\Fusion\amd64_microsoft.vc90.mfc_1fc8b3b9a1e18e3b_none_a2f75f3b9d7989e4\9.0\9.0.30729.6161\mfc90u.dll",
    ],
    [
        r"C:\Windows\WinSxS\Fusion\amd64_microsoft.vc90.mfc_1fc8b3b9a1e18e3b_none_a2f75f3b9d7989e4\9.0\9.0.30729.6161\mfc90u.dll",
        r"C:\Windows\WinSxS\Fusion\amd64_microsoft.vc90.mfc_1fc8b3b9a1e18e3b_none_a2f75f3b9d7989e4\9.0\9.0.30729.6161\mfc90.dll",
    ],
    [
        r"C:\Windows\WinSxS\amd64_microsoft.vc90.crt_1fc8b3b9a1e18e3b_9.0.30729.9635_none_08e2c157a83ed5da\msvcr90.dll",
        r"C:\Windows\WinSxS\Fusion\amd64_microsoft.vc90.crt_1fc8b3b9a1e18e3b_none_a28692199dcba471\9.0\9.0.30729.6161\msvcr90.dll",
    ],
    [
        r"C:\Windows\WinSxS\amd64_microsoft.vc90.crt_1fc8b3b9a1e18e3b_9.0.30729.9635_none_08e2c157a83ed5da\msvcp90.dll",
        r"C:\Windows\WinSxS\Fusion\amd64_microsoft.vc90.crt_1fc8b3b9a1e18e3b_none_a28692199dcba471\9.0\9.0.30729.6161\msvcp90.dll",
    ],
]

added_vc90 = set()
for candidates in vc90_candidate_groups:
    dll = first_existing(*candidates)
    if dll and dll not in added_vc90:
        binaries.append((dll, "library/windows/x86_64"))
        added_vc90.add(dll)


a = Analysis(
    ["main.py"],
    pathex=[],
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
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="main",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
