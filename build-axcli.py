#!/usr/bin/env python3
"""Build the pinned upstream AXCLI revision with the local Chromium initialization fix."""

from pathlib import Path
import io
import os
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parent
REVISION = "ce6ef8e65d210fbcf4f8d3d8d8d4bbeda5d2fde6"


def build() -> None:
    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    candidates = list((cache / "cargo/git/checkouts").glob("axcli-*/*"))
    repository = next((p for p in candidates if (p / "Cargo.toml").is_file()), None)
    if repository is None:
        repository = cache / "axcli-upstream"
        if not repository.exists():
            subprocess.run(["git", "clone", "--no-checkout", "https://github.com/andelf/axcli", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "cat-file", "-e", REVISION + "^{commit}"], check=True)
    archive = subprocess.check_output(["git", "-C", str(repository), "archive", REVISION])
    environment = os.environ.copy()
    environment["CARGO_HOME"] = str(cache / "cargo")
    environment["CARGO_TARGET_DIR"] = str(cache / "cargo-target")
    with tempfile.TemporaryDirectory(prefix="axcli-build-", dir=cache) as directory:
        source = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as package:
            package.extractall(source, filter="data")
        subprocess.run(["git", "apply", str(ROOT / "patches/chromium-accessibility.patch")], cwd=source, check=True)
        subprocess.run([
            "cargo", "install", "--path", str(source), "--locked", "--force",
            "--root", str(ROOT / ".tools/axcli"),
        ], env=environment, check=True)


if __name__ == "__main__":
    build()
