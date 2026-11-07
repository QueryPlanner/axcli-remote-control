# AXCLI Remote Control API

FastAPI API for controlling the active macOS desktop and inspecting individual apps with AXCLI. The server binds only to the Mac's Tailscale IPv4 address. Control and capture requests require an HTTP Bearer token.

Version 0.3.0 supports full-display screenshots and system-wide keyboard and mouse control.

## Install

From this directory:

```sh
./setup.sh
mkdir -p .secrets
umask 077
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > .secrets/api_token
chmod 600 .secrets/api_token
```

`setup.sh` installs FastAPI/Uvicorn into `.venv` and builds AXCLI from upstream revision `ce6ef8e65d210fbcf4f8d3d8d8d4bbeda5d2fde6` into `.tools/axcli`. `build-axcli.py` applies the checked-in Chromium accessibility patch before building. It enables Chromium/Electron accessibility when these apps are targeted and skips self-referencing AX children during traversal. Setup also compiles the macOS desktop helper using Apple's command-line developer tools.

## Start

```sh
./run.sh
```

`run.sh` discovers the `100.64.0.0/10` IPv4 address on a `utun` interface and refuses to start if none is found. The default port is `8765`; set `AXCLI_PORT` to change it. The server is reachable at `http://<tailscale-ip>:8765` from devices allowed to connect over the same tailnet.

Run it from a logged-in user session and keep that process alive. The Accessibility APIs require the Mac's active user session and grants.

### Dedicated macOS app

The app gives the server its own macOS permission identity. This avoids attributing AXCLI's requests to whichever terminal or coding app originally started the server.

```sh
python3 build-app.py
python3 install-app.py
```

`build-app.py` creates `dist/AXCLI Remote.app`, containing the API, dependencies, AXCLI, and a native launcher. It uses the existing Homebrew Python installation. `install-app.py` copies the app to `~/Applications/AXCLI Remote.app` and preserves the API token in `~/Library/Application Support/AXCLI Remote/api_token`. It does not create a login item by default.

Stop any existing `run.sh` server before starting the app:

```sh
open -g "$HOME/Applications/AXCLI Remote.app"
```

If explicitly desired, `python3 install-app.py --start-at-login` also creates `~/Library/LaunchAgents/local.axcli.remote.plist`. Load that optional login agent from the logged-in user's session:

```sh
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.axcli.remote.plist"
```

The app keeps port `8765`, discovers the current Tailscale address, and refuses to bind elsewhere. Logs are written to `~/Library/Application Support/AXCLI Remote/server.log`. Homebrew Python must remain installed; rebuild the app after updating its Python version or bundled dependencies.

## API

`GET /healthz` reports whether the server and AXCLI executable are present. The `/v1` endpoints require `Authorization: Bearer <token>`.

| Method | Path | Request fields |
| --- | --- | --- |
| GET | `/v1/apps` | — |
| POST | `/v1/snapshot` | `app`, optional `selector`, optional `depth` (default 10), optional `all` |
| POST | `/v1/click` | `app`, `selector`, optional `strategy` (`auto`, `ax`, `cg-pid`) |
| POST | `/v1/double-click` | `app`, `selector` |
| POST | `/v1/input` | `app`, `selector`, `text` (append) |
| POST | `/v1/fill` | `app`, `selector`, `text` (replace) |
| POST | `/v1/press` | `app`, `key` (app-targeted `pid` strategy) |
| POST | `/v1/get` | `app`, `selector`, optional `attribute` |
| POST | `/v1/wait` | `app`, `selector`, optional `timeout_ms` |
| POST | `/v1/activate` | `app` |
| GET | `/v1/displays` | — |
| POST | `/v1/screenshot` | `{}` for the main display, `display_id` for another display, or `app` and optional `selector` for a window or element |
| GET | `/v1/desktop/mouse` | — (returns current cursor coordinates) |
| POST | `/v1/desktop/mouse` | `action`, `x`, `y`, plus fields for scrolling or dragging |
| POST | `/v1/desktop/press` | `key` (sent to the current system focus) |
| POST | `/v1/desktop/type` | `text` (typed at the current system focus) |

Set `API_BASE` to the Mac's Tailscale IP before making requests.

```sh
API_BASE=http://YOUR_TAILSCALE_IP:8765
curl "$API_BASE/healthz"
curl -H "Authorization: Bearer $(cat .secrets/api_token)" \
  -H 'Content-Type: application/json' \
  -d '{"app":"Safari","depth":4}' \
  "$API_BASE/v1/snapshot"
```

### Control the desktop

Send `{}` to `POST /v1/screenshot` to capture the entire main display, including the menu bar, visible windows, and Dock when shown. Send `{"display_id":1}` with an ID from `GET /v1/displays` to select a display. Decode `png_base64` as a PNG. Display capture uses ScreenCaptureKit and requires macOS 15.2 or later. Capture and input operate in the logged-in macOS session.

A desktop capture returns its `display` bounds in screen points and its `image_width` and `image_height` in pixels. Use these to convert image coordinates `(px, py)` to mouse coordinates. The origin is the top-left of the main display, and secondary displays can have negative coordinates.

```text
x = display.x + px * display.width / image_width
y = display.y + py * display.height / image_height
```

Send mouse actions to `POST /v1/desktop/mouse`:

```json
{"action":"click","x":520,"y":230}
{"action":"right-click","x":520,"y":230}
{"action":"scroll","x":520,"y":230,"dy":-400}
{"action":"drag","x":520,"y":230,"to_x":670,"to_y":400}
```

`move` and `double-click` take the same fields as `click`. For scrolling, positive `dy` scrolls up and positive `dx` scrolls left. Send shortcuts like `{"key":"Command+Space"}` to `POST /v1/desktop/press` and literal text like `{"text":"Finder"}` to `POST /v1/desktop/type`. These actions share the Mac's cursor and keyboard focus with the person using the desktop.

### Brave and Chromium locators

Use the exact bundle ID `com.brave.Browser` to avoid matching browser helper applications. Chromium builds its complete accessibility tree when an assistive client requests it. The bundled AXCLI patch does that automatically; the server's macOS permission alone does not initialize the browser's tree.

To inspect all text fields, send this to `POST /v1/snapshot`:

```json
{"app":"com.brave.Browser","selector":"AXTextField","depth":2,"all":true}
```

Brave's address bar uses the locator `AXTextField[desc="Address and search bar"]` (also reachable by `.BraveOmniboxViewViews`). Use `desc`, rather than a Playwright locator expression or a role name containing spaces. For example, send this to `POST /v1/fill`:

```json
{"app":"com.brave.Browser","selector":"AXTextField[desc=\"Address and search bar\"]","text":"https://www.linkedin.com/"}
```

Then send `{"app":"com.brave.Browser","key":"Enter"}` to `POST /v1/press`. Other valid locators include `AXButton[title="Restore"]`, `.DOMClass`, `text="Exact text"`, and chained locators separated by ` >> `. A full browser toolbar may be hidden in a depth-5 snapshot; inspect the specific selector or use the default depth of 10. Syntax examples are also included in the OpenAPI schema at `/openapi.json` and the interactive `/docs` page.

Brave snapshots return one application root, and the address-bar locator works with `/v1/fill` and `/v1/get`. Invalid locator syntax returns 422 with a syntax example, and requests without a bearer token return 401.

## macOS permissions

Open **System Settings → Privacy & Security → Device Control and Data Access** (the pane previously called Accessibility). For the dedicated app, add and enable `~/Applications/AXCLI Remote.app`.

macOS attributes permission requests to the responsible application. When `run.sh` is started from a terminal or coding app, that parent application may need permission even if the standalone `axcli` executable is enabled. The observed denial for the original server was attributed to `ChatGPT.app` in macOS's TCC logs. The dedicated app gives the server a separate identity.

For `/v1/screenshot`, also enable the identified app under **Privacy & Security → Screen & System Audio Recording**. After replacing a locally signed build, macOS may retain a grant for the previous signature. Remove and re-add the installed app in both permission lists if either grant stops working, then restart AXCLI Remote. A missing Screen Recording grant returns HTTP 424.
