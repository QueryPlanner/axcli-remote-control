"""Run the API inside the dedicated macOS application's process tree."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import subprocess

import uvicorn


def tailscale_address() -> str:
    interfaces = subprocess.check_output(["/sbin/ifconfig", "-a"], text=True)
    is_utun = False
    network = ipaddress.ip_network("100.64.0.0/10")
    for line in interfaces.splitlines():
        if line and not line[0].isspace():
            is_utun = line.split(":", 1)[0].startswith("utun")
        fields = line.split()
        if is_utun and fields[:1] == ["inet"] and len(fields) > 1:
            address = ipaddress.ip_address(fields[1])
            if address in network:
                return str(address)
    raise RuntimeError("No Tailscale IPv4 address found; refusing to bind elsewhere")


if __name__ == "__main__":
    resources = Path(__file__).resolve().parent
    uvicorn.run(
        "main:app",
        app_dir=str(resources),
        host=tailscale_address(),
        port=int(os.environ.get("AXCLI_PORT", "8765")),
    )
