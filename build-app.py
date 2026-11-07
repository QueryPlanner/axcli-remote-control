#!/usr/bin/env python3
"""Build an app whose macOS permission identity belongs to the AXCLI API."""

from pathlib import Path
import plistlib
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent
BUNDLE = ROOT / "dist" / "AXCLI Remote.app"


def build() -> None:
    python = (ROOT / ".venv/bin/python").resolve(strict=True)
    site_packages = next((ROOT / ".venv/lib").glob("python*/site-packages"))
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    contents = BUNDLE / "Contents"
    executable = contents / "MacOS" / "AXCLIRemote"
    resources = contents / "Resources"
    executable.parent.mkdir(parents=True)
    resources.mkdir()
    with (contents / "Info.plist").open("wb") as output:
        plistlib.dump({
            "CFBundleIdentifier": "local.axcli.remote",
            "CFBundleExecutable": "AXCLIRemote",
            "CFBundleName": "AXCLI Remote",
            "CFBundleDisplayName": "AXCLI Remote",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": "3",
            "CFBundleShortVersionString": "0.3.0",
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "NSAccessibilityUsageDescription": "Allow authenticated AXCLI API requests to control this Mac's desktop.",
            "NSScreenCaptureUsageDescription": "Allow authenticated AXCLI API requests to take screenshots of this Mac's desktop.",
        }, output)
    subprocess.run([
        "/usr/bin/xcrun", "clang", "-fobjc-arc", "-framework", "Cocoa",
        str(ROOT / "macos/Launcher.m"), "-o", str(executable),
    ], check=True)
    shutil.copytree(site_packages, resources / "python", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "main.py", resources / "main.py")
    shutil.copy2(ROOT / "macos/bootstrap.py", resources / "bootstrap.py")
    shutil.copy2(ROOT / ".tools/axcli/bin/axcli", resources / "axcli")
    subprocess.run(["/bin/sh", str(ROOT / "macos/build-desktop.sh"), str(resources / "desktop-control")], check=True)
    (resources / "python-executable.txt").write_text(str(python) + "\n")
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(BUNDLE)], check=True)
    print(BUNDLE)


if __name__ == "__main__":
    build()
