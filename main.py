from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


APP_TITLE = "AXCLI Remote Control"
MAX_TEXT_CHARS = 20_000
MAX_OUTPUT_CHARS = 300_000
MAX_IMAGE_BYTES = 15_000_000
DESKTOP_BINARY = Path(__file__).with_name("desktop-control")
Coordinate = Annotated[float, Field(ge=-1_000_000, le=1_000_000)]
CALL_LOCK = threading.Lock()
bearer = HTTPBearer(auto_error=False)

app = FastAPI(
    title=APP_TITLE,
    version="0.3.0",
    description="Authenticated macOS desktop control and app accessibility over Tailscale.",
    docs_url="/docs",
    redoc_url=None,
)
api = APIRouter(prefix="/v1")


def _configured_token() -> str | None:
    token = os.environ.get("AXCLI_API_TOKEN")
    token_file = os.environ.get("AXCLI_TOKEN_FILE")
    if token:
        return token.strip()
    if token_file:
        try:
            return Path(token_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return None


async def require_bearer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    expected = _configured_token()
    if not expected:
        raise HTTPException(status_code=503, detail="API token is not configured")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer token required")
    if not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=401, detail="Invalid bearer token")


@app.middleware("http")
async def reject_cross_origin_browser_requests(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlsplit

        request_host = request.headers.get("host", "").lower()
        origin_host = (urlsplit(origin).netloc or "").lower()
        if not request_host or origin_host != request_host:
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-origin request rejected"},
            )
    return await call_next(request)


class AppTarget(BaseModel):
    app: str = Field(
        min_length=1, max_length=120,
        description="Exact running app name or bundle ID. Prefer com.brave.Browser for Brave.",
        examples=["com.brave.Browser", "System Settings"],
    )

    @field_validator("app")
    @classmethod
    def validate_app(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("app must be a non-empty application name")
        return value


class SelectorTarget(AppTarget):
    selector: str = Field(
        min_length=1, max_length=2_000,
        description='AXCLI locator, e.g. AXTextField[desc="Address and search bar"]. Supports AX roles, .DOMClass, text="value", and chaining with >>. Use desc for AXDescription; Playwright getByRole() syntax is not supported.',
        examples=['AXTextField[desc="Address and search bar"]'],
    )

    @field_validator("selector")
    @classmethod
    def validate_selector(cls, value: str) -> str:
        if value.startswith("-"):
            raise ValueError("selector cannot begin with a command-line option")
        return value


class SnapshotRequest(AppTarget):
    selector: str | None = Field(
        default=None, max_length=2_000,
        description="Optional AXCLI locator. To inspect browser controls directly, use AXTextField or .BraveOmniboxViewViews.",
        examples=["AXTextField", '.BraveOmniboxViewViews'],
    )
    depth: int = Field(default=10, ge=1, le=20, description="Chromium toolbar controls can be more than 5 levels deep.")
    all: bool = Field(default=False, description="When a selector is supplied, show every match instead of only the first.")


class TextInputRequest(SelectorTarget):
    text: str = Field(max_length=MAX_TEXT_CHARS)


class ClickRequest(SelectorTarget):
    strategy: Literal["auto", "ax", "cg-pid"] = "cg-pid"


class PressRequest(AppTarget):
    key: str = Field(min_length=1, max_length=100, description="Key combo such as Command+L, Command+T, or Enter.", examples=["Command+L", "Enter"])
    strategy: Literal["pid"] = "pid"


class GetRequest(SelectorTarget):
    attribute: Literal["text", "value", "title", "classes", "position", "size"] = "text"


class WaitRequest(SelectorTarget):
    timeout_ms: int = Field(default=10_000, ge=0, le=60_000)


class AppScreenshotRequest(AppTarget):
    model_config = ConfigDict(extra="forbid")
    selector: str | None = Field(default=None, max_length=2_000, description="Optional AXCLI element locator.")


class DesktopScreenshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_id: int | None = Field(default=None, strict=True, gt=0, le=2**32 - 1, description="ID from GET /v1/displays. Omit for the main display.")


class Display(BaseModel):
    id: int
    main: bool
    x: float
    y: float
    width: float
    height: float


class ScreenshotResponse(BaseModel):
    ok: bool = True
    content_type: Literal["image/png"] = "image/png"
    png_base64: str
    image_width: int
    image_height: int
    display: Display | None = None


class DesktopPressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=100, examples=["Command+Space", "Escape"])

    @field_validator("key")
    @classmethod
    def validate_key(cls, key: str) -> str:
        modifiers = {"control", "ctrl", "shift", "option", "alt", "command", "cmd", "super"}
        named_keys = {
            "return", "enter", "tab", "space", "delete", "backspace", "escape", "esc",
            "left", "right", "down", "up", "home", "end", "pageup", "pagedown",
            *(f"f{number}" for number in range(1, 13)),
        }
        single_keys = set("abcdefghijklmnopqrstuvwxyz0123456789=-[]';\\,/.")
        *prefix, name = (part.strip().lower() for part in key.split("+"))
        if not all(modifier in modifiers for modifier in prefix) or name not in named_keys | single_keys:
            raise ValueError("Use an AXCLI key such as Command+Space, Control+Shift+V, or Escape")
        return "+".join([*prefix, name])


class DesktopTypeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS, pattern=r"^[^\x00]+$")


class Pointer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: Coordinate
    y: Coordinate


class PointerMove(Pointer):
    action: Literal["move"]


class PointerClick(Pointer):
    action: Literal["click", "double-click", "right-click"]


class PointerScroll(Pointer):
    action: Literal["scroll"]
    dx: int = Field(default=0, ge=-10_000, le=10_000, description="Horizontal scroll delta. Positive scrolls left.")
    dy: int = Field(default=0, ge=-10_000, le=10_000, description="Vertical scroll delta. Positive scrolls up.")


class PointerDrag(Pointer):
    action: Literal["drag"]
    to_x: Coordinate
    to_y: Coordinate


MouseAction = Annotated[PointerMove | PointerClick | PointerScroll | PointerDrag, Field(discriminator="action")]


def _axcli_path() -> str | None:
    configured = os.environ.get("AXCLI_BIN", "axcli")
    candidate = shutil.which(configured) if not os.path.isabs(configured) else configured
    if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def _run_command(command: list[str], timeout: float, tool_name: str) -> subprocess.CompletedProcess[str]:
    try:
        with CALL_LOCK:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise HTTPException(status_code=504, detail=f"{tool_name} command timed out") from error
    except OSError as error:
        raise HTTPException(status_code=503, detail=f"Could not start {tool_name}: {error}") from error


def _run_axcli(args: list[str], timeout: float = 30.0) -> dict[str, object]:
    executable = _axcli_path()
    if not executable:
        raise HTTPException(
            status_code=503,
            detail="AXCLI is not installed or AXCLI_BIN does not point to an executable",
        )
    result = _run_command([executable, *args], timeout, "AXCLI")

    stdout = result.stdout[-MAX_OUTPUT_CHARS:]
    stderr = result.stderr[-MAX_OUTPUT_CHARS:]
    if result.returncode != 0:
        detail = stderr.strip() or stdout.strip() or f"AXCLI exited with status {result.returncode}"
        if "unrecognized locator syntax" in detail or "bracket selector" in detail:
            detail += '\nUse AXCLI syntax, e.g. AXTextField[desc="Address and search bar"]. See /docs for examples.'
        status = 424 if any(
            text in detail.lower()
            for text in (
                "accessibility permission",
                "accessibility not granted",
                "not trusted",
                "screen recording",
                "not authorized",
            )
        ) else 422
        raise HTTPException(status_code=status, detail=detail[-8_000:])
    return {"ok": True, "stdout": stdout, "stderr": stderr}


async def _invoke(args: list[str], timeout: float = 30.0) -> dict[str, object]:
    return await asyncio.to_thread(_run_axcli, args, timeout)


async def _desktop(*args: str) -> str:
    executable = os.environ.get("AXCLI_DESKTOP_BIN", str(DESKTOP_BINARY))
    result = await asyncio.to_thread(_run_command, [executable, *args], 20.0, "macOS desktop helper")
    if result.returncode:
        status = {10: 424, 11: 404, 12: 502, 13: 504, 14: 502}.get(result.returncode, 502)
        raise HTTPException(status_code=status, detail=result.stderr.strip() or "macOS desktop action failed")
    return result.stdout


def _app_args(app_name: str) -> list[str]:
    return ["--app", app_name]


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {
        "ok": True,
        "service": "axcli-api",
        "axcli_available": _axcli_path() is not None,
        "authentication_configured": bool(_configured_token()),
    }


@api.get("/apps")
async def list_apps() -> dict[str, object]:
    return await _invoke(["list-apps"])


@api.post("/snapshot")
async def snapshot(request: SnapshotRequest) -> dict[str, object]:
    args = _app_args(request.app) + ["snapshot"]
    if request.selector is not None:
        if request.selector.startswith("-"):
            raise HTTPException(status_code=422, detail="selector cannot begin with a command-line option")
        args.append(request.selector)
    args += ["--depth", str(request.depth)]
    if request.all:
        args.append("--all")
    return await _invoke(args)


@api.post("/click")
async def click(request: ClickRequest) -> dict[str, object]:
    return await _invoke(
        _app_args(request.app) + ["click", request.selector, "--strategy", request.strategy]
    )


@api.post("/double-click")
async def double_click(request: SelectorTarget) -> dict[str, object]:
    return await _invoke(_app_args(request.app) + ["dblclick", request.selector])


@api.post("/input")
async def input_text(request: TextInputRequest) -> dict[str, object]:
    return await _invoke(_app_args(request.app) + ["input", request.selector, request.text])


@api.post("/fill")
async def fill_text(request: TextInputRequest) -> dict[str, object]:
    return await _invoke(_app_args(request.app) + ["fill", request.selector, request.text])


@api.post("/press")
async def press_key(request: PressRequest) -> dict[str, object]:
    return await _invoke(
        _app_args(request.app) + ["press", request.key, "--strategy", request.strategy]
    )


@api.post("/get")
async def get_attribute(request: GetRequest) -> dict[str, object]:
    return await _invoke(
        _app_args(request.app) + ["get", request.attribute, request.selector]
    )


@api.post("/wait")
async def wait_for_element(request: WaitRequest) -> dict[str, object]:
    return await _invoke(
        _app_args(request.app) + ["wait", request.selector],
        timeout=max(5.0, request.timeout_ms / 1_000 + 5.0),
    )


@api.post("/activate")
async def activate(request: AppTarget) -> dict[str, object]:
    return await _invoke(_app_args(request.app) + ["activate"])


@api.get("/displays")
async def displays() -> list[Display]:
    """List active displays with bounds in global screen points."""
    return [Display.model_validate(display) for display in json.loads(await _desktop("displays"))]


@api.post("/screenshot", response_model_exclude_none=True)
async def screenshot(request: Annotated[
    DesktopScreenshotRequest | AppScreenshotRequest,
    Body(openapi_examples={
        "desktop": {"summary": "Main display", "value": {}},
        "display": {"summary": "Display by ID", "value": {"display_id": 1}},
        "app": {"summary": "Brave window", "value": {"app": "com.brave.Browser"}},
    }),
]) -> ScreenshotResponse:
    """Send {} for the main display, display_id for another display, or app for an AXCLI capture."""
    display = None
    with tempfile.TemporaryDirectory(prefix="axcli-") as directory:
        path = Path(directory) / "screenshot.png"
        if isinstance(request, DesktopScreenshotRequest):
            display_id = "main" if request.display_id is None else str(request.display_id)
            display = Display.model_validate_json(await _desktop("screenshot", display_id, str(path)))
        else:
            args = _app_args(request.app) + ["screenshot"]
            if request.selector:
                if request.selector.startswith("-") or "\0" in request.selector:
                    raise HTTPException(status_code=422, detail="Invalid screenshot selector")
                args.append(request.selector)
            await _desktop("check-screen")
            await _invoke([*args, "-o", str(path)], timeout=60.0)
        try:
            with path.open("rb") as output:
                image = output.read(MAX_IMAGE_BYTES + 1)
        except FileNotFoundError as error:
            raise HTTPException(status_code=502, detail="macOS did not produce a screenshot") from error
    if len(image) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Screenshot exceeded the 15 MB response limit")
    if len(image) < 33 or image[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise HTTPException(status_code=502, detail="macOS did not produce a valid PNG")
    width, height = struct.unpack_from(">II", image, 16)
    return ScreenshotResponse(
        png_base64=base64.b64encode(image).decode("ascii"),
        image_width=width, image_height=height, display=display,
    )


@api.post("/desktop/press")
async def desktop_press(request: DesktopPressRequest) -> dict[str, bool]:
    """Send a shortcut or key to the current system focus."""
    await _invoke(["keyboard", "press", "--", request.key])
    return {"ok": True}


@api.post("/desktop/type")
async def desktop_type(request: DesktopTypeRequest) -> dict[str, bool]:
    """Type literal text into the current system focus."""
    await _invoke(["keyboard", "type", "--", request.text])
    return {"ok": True}


@api.get("/desktop/mouse")
async def pointer_position() -> Pointer:
    """Return the cursor position in global screen points."""
    result = await _invoke(["mouse", "pos"])
    x, y = str(result["stdout"]).strip().split(",")
    return Pointer(x=x, y=y)


@api.post("/desktop/mouse")
async def desktop_mouse(request: MouseAction) -> dict[str, bool]:
    """Move, click, scroll, or drag in global screen points."""
    point = [str(request.x), str(request.y)]
    if isinstance(request, PointerDrag):
        await _desktop("drag", *point, str(request.to_x), str(request.to_y))
    elif request.action == "right-click":
        await _desktop("right-click", *point)
    elif isinstance(request, PointerScroll):
        await _invoke(["mouse", "scroll", str(request.dx), str(request.dy), *point])
    else:
        action = "dblclick" if request.action == "double-click" else request.action
        await _invoke(["mouse", action, *point])
    return {"ok": True}


@app.on_event("startup")
async def configure_api_auth() -> None:
    # Fail closed if the API is accidentally started without a token.
    if not _configured_token():
        raise RuntimeError("Set AXCLI_TOKEN_FILE or AXCLI_API_TOKEN before starting the server")


app.include_router(api, dependencies=[Depends(require_bearer)])
