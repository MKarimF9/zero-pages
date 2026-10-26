"""The reader's buttons on your phone: a web page with up / select / down / hold buttons.
Runs inside the reader (the boot service) on its own port.

  - the page itself is public; every other request needs the PIN (X-PIN header);
  - the PIN is six digits kept in ~/.pi-ereader/remote-pin (read it over SSH: cat that file);
  - wrong guesses lock the page for a minute, as on the upload page;
  - it can press up, down, select and hold-select. It cannot quit or switch the Pi off.
"""
from __future__ import annotations

import hmac
import json
import os
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .controls import Button, Controls

PORT = 8090
MAX_FAILURES, LOCKOUT_SECONDS = 5, 60
PRESSES = {"up": Button.UP, "down": Button.DOWN, "select": Button.SELECT, "hold": Button.SELECT_LONG}


def pin_path() -> Path:
    return Path.home() / ".pi-ereader" / "remote-pin"


def load_pin(path: str | Path) -> str:
    """The saved PIN, or a new random six-digit one saved for the owner only."""
    path = Path(path)
    try:
        pin = path.read_text().strip()
        if pin.isdigit() and len(pin) >= 4:
            return pin
    except OSError:
        pass
    pin = f"{secrets.randbelow(10**6):06d}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(pin + "\n")
    except OSError:
        pass   # a read-only card: the PIN lasts until the reader restarts
    return pin


class QueueControls(Controls):
    """Buttons pressed from code (the web page) instead of by hand."""

    def __init__(self) -> None:
        self._events: queue.Queue[Button] = queue.Queue()

    def press(self, button: Button) -> None:
        self._events.put(button)

    def wait(self) -> Button:
        return self._events.get()


class WebRemote:
    def __init__(self, controls: QueueControls, pin: str, host: str = "0.0.0.0",
                 port: int = PORT, lockout: float = LOCKOUT_SECONDS, clock=time.monotonic):
        self.controls, self.pin = controls, pin
        self._lockout, self._clock = lockout, clock
        self._lock = threading.Lock()
        self._failures, self._locked_until = 0, 0.0
        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.httpd.app = self
        self.port = self.httpd.server_address[1]
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self.httpd.shutdown()
            self._thread.join(timeout=5)
        self.httpd.server_close()

    def check_pin(self, given: str) -> tuple[bool, float]:
        """(allowed, seconds to wait if locked out). Locked-out requests are refused even with
        the right PIN, or a guesser could just keep trying."""
        with self._lock:
            now = self._clock()
            if now < self._locked_until:
                return False, self._locked_until - now
            if hmac.compare_digest(given.encode(), self.pin.encode()):
                self._failures = 0
                return True, 0.0
            self._failures += 1
            if self._failures >= MAX_FAILURES:
                self._failures, self._locked_until = 0, now + self._lockout
            return False, 0.0


class _Handler(BaseHTTPRequestHandler):
    server_version = "pi-ereader-remote"
    protocol_version = "HTTP/1.0"

    def log_message(self, *args) -> None:
        pass

    @property
    def app(self) -> WebRemote:
        return self.server.app

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _authorized(self) -> bool:
        ok, wait = self.app.check_pin(self.headers.get("X-PIN", ""))
        if not ok:
            self._json(429 if wait else 403, {"error": f"Too many wrong PINs. Try again in {int(wait) + 1} seconds."
                                              if wait else "Wrong PIN."})
        return ok

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/api/check":   # does the PIN fit? (the page unlocks on a 200)
            if self._authorized():
                self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path != "/api/press":
            return self._json(404, {"error": "Not found."})
        if not self._authorized():
            return
        button = PRESSES.get(parse_qs(url.query).get("button", [""])[0])
        if button is None:
            return self._json(400, {"error": "The button must be up, down, select or hold."})
        self.app.controls.press(button)
        self._json(200, {"ok": True})


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>E-reader remote</title>
<style>
  :root { --bg:#f4f1ea; --fg:#1d1b17; --card:#fff; --line:#d7d1c3; --accent:#2b5d4f; --accent-fg:#fff; --bad:#a3352b; }
  @media (prefers-color-scheme: dark) { :root { --bg:#16140f; --fg:#ece7da; --card:#211e17; --line:#3a352a; --accent:#6fb59f; --accent-fg:#0d1a15; --bad:#e1796e; } }
  * { box-sizing:border-box; }
  body { margin:0; padding:16px; background:var(--bg); color:var(--fg); font:16px/1.4 system-ui, sans-serif; max-width:480px; margin-inline:auto; }
  h1 { font-size:1.1rem; margin:0 0 12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:14px; }
  .pad { display:grid; grid-template-columns:1fr; gap:10px; }
  button { font:inherit; font-weight:600; border:1px solid var(--line); background:var(--card); color:var(--fg); border-radius:12px; min-height:64px; touch-action:manipulation; }
  button.main { background:var(--accent); color:var(--accent-fg); border-color:var(--accent); }
  button:active { transform:scale(.98); }
  input { font:inherit; width:100%; padding:12px; border:1px solid var(--line); border-radius:10px; background:var(--bg); color:var(--fg); margin:8px 0; }
  #msg { color:var(--bad); min-height:1.4em; margin:4px 0 0; }
  small { opacity:.7; }
</style></head><body>
<h1>E-reader remote</h1>
<div class="card" id="pinbox" hidden>
  <label for="pin">PIN (on the Pi: <code>cat ~/.pi-ereader/remote-pin</code>)</label>
  <input id="pin" type="password" inputmode="numeric" autocomplete="off" maxlength="12">
  <button class="main" id="go" style="width:100%">Unlock</button>
</div>
<div id="app" hidden>
  <div class="pad">
    <button id="up">Up</button>
    <button class="main" id="select">Select</button>
    <button id="down">Down</button>
    <button id="hold">Hold select (options)</button>
  </div>
  <p><small>The screen takes a second or two to change.</small></p>
</div>
<p id="msg" role="status"></p>
<script>
const $ = s => document.querySelector(s);
// The phone keeps the PIN, so you type it once. (Storage can be blocked, e.g. in a private tab.)
const store = {get: () => { try { return localStorage.getItem('remote-pin') || ''; } catch (e) { return ''; } },
               set: v => { try { localStorage.setItem('remote-pin', v); } catch (e) {} },
               clear: () => { try { localStorage.removeItem('remote-pin'); } catch (e) {} }};
let pin = store.get();
const msg = t => { $('#msg').textContent = t || ''; };
function lock(text) { $('#pinbox').hidden = false; $('#app').hidden = true; store.clear(); pin = ''; msg(text); }
async function api(path, opts) {
  const r = await fetch(path, Object.assign({headers: {'X-PIN': pin}}, opts));
  if (r.status === 403 || r.status === 429) { lock((await r.json()).error); throw new Error('locked'); }
  return r;
}
async function press(b) {
  try { await api('/api/press?button=' + b, {method: 'POST'}); msg(''); }
  catch (e) { if (e.message !== 'locked') msg('Could not reach the reader.'); }
}
function unlocked() { $('#pinbox').hidden = true; $('#app').hidden = false; msg(''); }
$('#go').onclick = async () => { pin = $('#pin').value.trim();
  try { await api('/api/check'); store.set(pin); unlocked(); } catch (e) {} };
$('#pin').addEventListener('keydown', e => { if (e.key === 'Enter') $('#go').click(); });
for (const b of ['up', 'down', 'select', 'hold']) $('#' + b).onclick = () => press(b);
if (pin) api('/api/check').then(unlocked).catch(() => {}); else lock('');
</script></body></html>
"""
