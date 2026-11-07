#!/usr/bin/env python3
"""Install the built app; login startup is an explicit opt-in."""

import argparse
from pathlib import Path
import plistlib
import shutil


ROOT = Path(__file__).resolve().parent
HOME_DIRECTORY = Path.home()
BUNDLE = ROOT / "dist" / "AXCLI Remote.app"
DESTINATION = HOME_DIRECTORY / "Applications" / BUNDLE.name
STATE = HOME_DIRECTORY / "Library/Application Support/AXCLI Remote"
AGENT = HOME_DIRECTORY / "Library/LaunchAgents/local.axcli.remote.plist"


def install(start_at_login: bool = False) -> None:
    if not (BUNDLE / "Contents/MacOS/AXCLIRemote").is_file():
        raise SystemExit("Build the app with python3 build-app.py first")
    if DESTINATION.exists():
        with (DESTINATION / "Contents/Info.plist").open("rb") as source:
            if plistlib.load(source).get("CFBundleIdentifier") != "local.axcli.remote":
                raise SystemExit("Refusing to replace an unrelated application")
        shutil.rmtree(DESTINATION)
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUNDLE, DESTINATION)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.chmod(0o700)
    token = STATE / "api_token"
    existing_token = (ROOT / ".secrets/api_token").read_bytes()
    if token.exists() and token.read_bytes() != existing_token:
        raise SystemExit("Installed app has a different API token; refusing to replace it")
    if not token.exists():
        with token.open("xb") as output:
            token.chmod(0o600)
            output.write(existing_token)
    token.chmod(0o600)
    print("Application:", DESTINATION)
    if start_at_login:
        agent = {
            "Label": "local.axcli.remote",
            "ProgramArguments": ["/usr/bin/open", "-g", str(DESTINATION)],
            "RunAtLoad": True,
            "LimitLoadToSessionType": "Aqua",
        }
        AGENT.parent.mkdir(parents=True, exist_ok=True)
        with AGENT.open("wb") as output:
            plistlib.dump(agent, output)
        AGENT.chmod(0o600)
        print("Login agent:", AGENT)
    print("Log:", STATE / "server.log")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-at-login", action="store_true")
    install(parser.parse_args().start_at_login)
