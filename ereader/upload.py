"""Manage books over Wi-Fi: a tiny web page on the Pi to add .epub files and to organise
the library (categories, an Up next list, pins, delete). The organising is in manager.py.

Only the standard library is used, to keep memory low on a Pi Zero. The server
is meant to run only while you're on the "Manage books over Wi-Fi" screen (see
UploadScreen in screens.py), not all the time. You can also run it on its own:

    python3 -m ereader.upload --books ~/books

Safety, since it listens on your home network:
  - every request except the page itself needs a PIN (shown on the e-reader);
    five wrong guesses lock the server for a minute
  - only files ending in .epub, with a plain name (no folders), up to a size
    limit, and the file must really open as an EPUB or it's thrown away
  - uploads are written to a temporary file and only moved into the books
    folder once checked, so a half-finished upload never shows up as a book
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import shutil
import socket
import threading
import time
import unicodedata
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .epub import Epub
from .manager import BookManager

PORT = 8080
MAX_UPLOAD_BYTES = 100 * 1024 * 1024   # a very large EPUB; the Pi's SD card is the real limit
DISK_RESERVE = 50 * 1024 * 1024        # always leave this much free
MAX_FAILURES, LOCKOUT_SECONDS = 5, 60  # wrong PIN guesses allowed, then the lock
MAX_JSON_BYTES = 4096                  # a change request is a few words


def local_ip() -> str | None:
    """The Pi's address on the local network, or None if it has none."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the right interface
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def safe_name(name: str) -> str | None:
    """The file name if it is safe to store in the books folder, else None."""
    name = unicodedata.normalize("NFC", name)
    if not name or len(name.encode()) > 200 or name.startswith("."):
        return None
    if any(c in name for c in "/\\") or any(ord(c) < 32 for c in name):
        return None
    if not name.lower().endswith(".epub") or len(name) <= len(".epub"):
        return None
    return name


class UploadServer:
    def __init__(self, books_dir: str | Path, pin: str | None = None, host: str = "0.0.0.0",
                 port: int = PORT, max_bytes: int = MAX_UPLOAD_BYTES, lockout: float = LOCKOUT_SECONDS,
                 clock=time.monotonic, manager: BookManager | None = None):
        self.books_dir = Path(books_dir).expanduser()
        self.pin = pin if pin is not None else f"{secrets.randbelow(10000):04d}"  # "" means no PIN
        self.max_bytes, self._lockout, self._clock = max_bytes, lockout, clock
        self.manager = manager      # lists and changes books and their saved state (None: upload only)
        self.added: list[str] = []  # names uploaded this session, for the screen
        self._failures, self._locked_until = 0, 0.0
        self._lock = threading.Lock()
        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.app = self
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def start(self) -> None:
        self.books_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self.httpd.shutdown()
            self._thread.join(timeout=5)
            self._thread = None
        self.httpd.server_close()

    def check_pin(self, given: str) -> tuple[bool, float]:
        """(allowed, seconds to wait if locked out). Locked-out requests are refused
        even with the right PIN, or a guesser could just keep trying."""
        if not self.pin:
            return True, 0.0
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

    def books(self) -> list[dict]:
        return [{"name": p.name, "size": p.stat().st_size}
                for p in sorted(self.books_dir.glob("*.epub"), key=lambda p: p.name.casefold())]


class _Handler(BaseHTTPRequestHandler):
    server_version = "pi-ereader"
    protocol_version = "HTTP/1.0"  # one request per connection: simple and low-memory

    def log_message(self, *args) -> None:  # keep the reader's terminal quiet
        pass

    # ---- helpers

    @property
    def app(self) -> UploadServer:
        return self.server.app

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict | list) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self.close_connection = True
        self._json(status, {"error": message})

    def _authorized(self) -> bool:
        ok, wait = self.app.check_pin(self.headers.get("X-PIN", ""))
        if not ok:
            self._error(429 if wait else 403,
                        f"Too many wrong PINs. Try again in {int(wait) + 1} seconds." if wait else "Wrong PIN.")
        return ok

    def _name(self) -> str | None:
        query = parse_qs(urlparse(self.path).query)
        name = safe_name(query.get("name", [""])[0])
        if name is None:
            self._error(400, "That file name isn't allowed. Use a plain name ending in .epub.")
        return name

    # ---- routes

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/api/info":
            free = shutil.disk_usage(self.app.books_dir).free
            self._json(200, {"pin_required": bool(self.app.pin), "free_mb": free // (1024 * 1024),
                             "max_mb": self.app.max_bytes // (1024 * 1024)})
        elif path == "/api/books":
            if self._authorized():
                self._json(200, self.app.books())
        elif path == "/api/library":
            if self.app.manager is None:
                self._error(404, "Not found.")
            elif self._authorized():
                self._json(200, self.app.manager.library())
        else:
            self._error(404, "Not found.")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in ("/api/upload", "/api/delete", "/api/update"):
            return self._error(404, "Not found.")
        if path == "/api/update" and self.app.manager is None:
            return self._error(404, "Not found.")
        if not self._authorized():
            return
        name = self._name()
        if name is None:
            return
        if path == "/api/delete":
            try:
                if self.app.manager:
                    self.app.manager.delete(name)   # also forgets its place, category and cached covers
                else:
                    target = self.app.books_dir / name
                    if not target.is_file():
                        raise KeyError(name)
                    target.unlink()
            except KeyError:
                return self._error(404, "No such book.")
            return self._json(200, {"ok": True})
        if path == "/api/update":
            return self._update(name)
        self._upload(name)

    def _update(self, name: str) -> None:
        try:
            length = int(self.headers.get("Content-Length", ""))
            if not 0 < length <= MAX_JSON_BYTES:
                raise ValueError
            changes = json.loads(self.rfile.read(length))
        except ValueError:
            return self._error(400, "Send a small JSON body.")
        try:
            self.app.manager.update(name, changes)
        except KeyError:
            return self._error(404, "No such book.")
        except ValueError as e:
            return self._error(400, str(e))
        self._json(200, {"ok": True})

    def _upload(self, name: str) -> None:
        app = self.app
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._error(411, "Content-Length is required.")
        if length <= 0:
            return self._error(400, "The file is empty.")
        if length > app.max_bytes:
            return self._error(413, f"Too big: the limit is {app.max_bytes // (1024 * 1024)} MB.")
        app.books_dir.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(app.books_dir).free < length + DISK_RESERVE:
            return self._error(507, "Not enough free space on the Pi.")

        tmp = app.books_dir / f".upload-{secrets.token_hex(4)}.part"  # hidden: never listed as a book
        try:
            status, body = self._receive(name, length, tmp)
        finally:
            tmp.unlink(missing_ok=True)  # clean up first, then answer: no leftovers once the client has a reply
        if status == 200:
            self._json(200, body)
        else:
            self._error(status, body["error"])

    def _receive(self, name: str, length: int, tmp: Path) -> tuple[int, dict]:
        """Write the body to `tmp`, check it, and move it into place: (status, reply)."""
        app = self.app
        try:
            remaining = length
            with open(tmp, "wb") as out:
                while remaining:
                    chunk = self.rfile.read(min(64 * 1024, remaining))
                    if not chunk:
                        return 400, {"error": "The upload was cut short."}
                    out.write(chunk)
                    remaining -= len(chunk)
            if not _is_epub(tmp):
                return 400, {"error": "That doesn't look like a valid EPUB."}
            dest = app.books_dir / name
            replaced = dest.exists()
            os.replace(tmp, dest)  # atomic: the book appears whole or not at all
            app.added.append(name)
            return 200, {"ok": True, "name": name, "replaced": replaced, "size": length}
        except OSError as e:
            return 500, {"error": f"Couldn't save the file: {e.strerror or e}"}


def _is_epub(path: Path) -> bool:
    if not zipfile.is_zipfile(path):
        return False
    epub = Epub(path)
    try:
        epub.open()
        return True
    except Exception:
        return False
    finally:
        epub.close()


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Your books</title>
<style>
  :root { --bg:#f6f5f1; --fg:#1c1b19; --mut:#6b6a66; --line:#d8d6cf; --card:#fff; --acc:#1c1b19; --bad:#b3261e; --ok:#1d6b3a; }
  @media (prefers-color-scheme: dark) { :root { --bg:#161513; --fg:#ecebe6; --mut:#9a9890; --line:#34322e; --card:#1f1e1b; --acc:#ecebe6; --bad:#f2b8b5; --ok:#8fd1a4; } }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.45 system-ui, sans-serif; }
  main { max-width:640px; margin:0 auto; padding:20px 16px 56px; }
  h1 { font-size:22px; margin:0 0 4px; } p.sub { color:var(--mut); margin:0 0 16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:12px; }
  input[type=file] { display:none; }
  input[type=password], input[type=text], select { font:inherit; padding:9px 10px; border:1px solid var(--line); border-radius:8px; background:var(--bg); color:var(--fg); max-width:100%; }
  input[type=password] { width:8em; }
  button { font:inherit; font-size:14px; padding:8px 12px; border-radius:8px; border:1px solid var(--line); background:var(--card); color:var(--fg); cursor:pointer; min-height:38px; }
  button.primary { background:var(--acc); color:var(--bg); border-color:var(--acc); }
  button.on { background:var(--acc); color:var(--bg); border-color:var(--acc); }
  button.del { color:var(--bad); }
  #chips { display:flex; gap:8px; overflow-x:auto; padding-bottom:6px; margin-bottom:10px; }
  #chips button { white-space:nowrap; border-radius:999px; }
  .book h3 { font-size:17px; margin:0; overflow-wrap:anywhere; } .book .au { color:var(--mut); font-size:14px; overflow-wrap:anywhere; }
  .book .st { color:var(--mut); font-size:14px; margin:6px 0 4px; overflow-wrap:anywhere; }
  .bar { height:6px; background:var(--line); border-radius:3px; overflow:hidden; margin-bottom:10px; } .bar i { display:block; height:100%; background:var(--acc); }
  .row { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:8px; }
  .tag { font-size:12px; border:1px solid var(--line); border-radius:999px; padding:1px 8px; color:var(--mut); }
  #drop { display:block; text-align:center; border:2px dashed var(--line); border-radius:12px; padding:22px 12px; cursor:pointer; }
  #drop.over { border-color:var(--acc); } #drop b { display:block; margin-bottom:2px; }
  ul { list-style:none; margin:0; padding:0; } li.up { display:flex; gap:12px; align-items:center; padding:8px 0; border-top:1px solid var(--line); }
  li.up:first-child { border-top:0; } li.up .n { flex:1; min-width:0; overflow-wrap:anywhere; } li.up .s { color:var(--mut); font-size:14px; white-space:nowrap; }
  progress { width:100%; height:6px; } .err { color:var(--bad); } .good { color:var(--ok); } .mut { color:var(--mut); }
  #msg { min-height:1.4em; margin-bottom:8px; } details > summary { cursor:pointer; font-weight:600; }
</style></head><body><main>
<h1>Your books</h1><p class="sub" id="sub">Organise what is on your e-reader.</p>

<div class="card" id="pinbox" hidden>
  <label for="pin">PIN shown on the e-reader</label><br>
  <input id="pin" type="password" inputmode="numeric" autocomplete="off" maxlength="12">
  <button class="primary" id="go">Unlock</button>
</div>
<div id="msg" class="err"></div>

<div id="app" hidden>
  <div id="chips"></div>
  <div id="books"></div>
  <details class="card"><summary>Add books</summary>
    <label id="drop"><b>Choose books or drop them here</b><span class="mut">.epub files only</span>
      <input id="file" type="file" accept=".epub,application/epub+zip" multiple></label>
    <ul id="uploads"></ul>
  </details>
</div>

<script>
const $ = s => document.querySelector(s);
let pin = sessionStorage.getItem('pin') || '', lib = {books: [], categories: []}, filter = sessionStorage.getItem('filter') || 'all';
const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };

async function api(path, opts = {}) {
  opts.headers = Object.assign({'X-PIN': pin}, opts.headers || {});
  const r = await fetch(path, opts); const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || r.statusText), {status: r.status});
  return j;
}
const change = async (name, body) => {
  try { await api('/api/update?name=' + encodeURIComponent(name), {method: 'POST', body: JSON.stringify(body)}); $('#msg').textContent = ''; }
  catch (e) { $('#msg').textContent = e.message; }
  refresh();
};
function show(unlocked) { $('#pinbox').hidden = unlocked; $('#app').hidden = !unlocked; }

const FILTERS = [['all', 'All'], ['want', 'Up next'], ['reading', 'Reading'], ['new', 'New'], ['finished', 'Finished']];
function matches(b) {
  if (filter === 'all') return true;
  if (filter === 'want') return b.queued;
  if (filter.startsWith('cat:')) return b.category === filter.slice(4);
  return b.status === filter;
}
function drawChips() {
  const chips = $('#chips'); chips.innerHTML = '';
  const all = FILTERS.concat(lib.categories.map(c => ['cat:' + c, c]));
  if (!all.some(f => f[0] === filter)) filter = 'all';
  for (const [key, label] of all) {
    const n = lib.books.filter(b => { const old = filter; filter = key; const m = matches(b); filter = old; return m; }).length;
    chips.append(el('button', {className: key === filter ? 'on' : '', textContent: label + ' ' + n,
      onclick: () => { filter = key; sessionStorage.setItem('filter', key); drawBooks(); drawChips(); }}));
  }
}
function card(b) {
  const status = b.status === 'new' ? 'New' : b.status === 'finished' ? 'Finished' : 'Reading · ' + b.progress + '%';
  const where = b.status === 'reading' && b.chapter ? ' · ' + b.chapter : '';
  const sel = el('select', {onchange: e => {
    let v = e.target.value;
    if (v === '__new') { v = (prompt('Category name') || '').trim(); if (!v) return refresh(); }
    change(b.name, {category: v === '__none' ? '' : v});
  }});
  sel.append(el('option', {value: '__none', textContent: 'No category'}));
  for (const c of lib.categories) sel.append(el('option', {value: c, textContent: c, selected: c === b.category}));
  sel.append(el('option', {value: '__new', textContent: 'New category…'}));
  if (!b.category) sel.value = '__none';
  const row = el('div', {className: 'row'},
    el('button', {className: b.queued ? 'on' : '', textContent: b.queued ? 'Up next #' + b.queue_position : 'Up next', onclick: () => change(b.name, {queued: !b.queued})}),
    el('button', {className: b.pinned ? 'on' : '', textContent: b.pinned ? 'Pinned' : 'Pin', onclick: () => change(b.name, {pinned: !b.pinned})}),
    sel);
  if (b.queued && filter === 'want') row.append(
    el('button', {textContent: '▲', title: 'Move up', onclick: () => change(b.name, {move: 'up'})}),
    el('button', {textContent: '▼', title: 'Move down', onclick: () => change(b.name, {move: 'down'})}));
  row.append(el('button', {className: 'del', textContent: 'Delete', onclick: async () => {
    if (!confirm('Delete ' + b.title + '? This removes the file from the e-reader.')) return;
    try { await api('/api/delete?name=' + encodeURIComponent(b.name), {method: 'POST'}); } catch (e) { $('#msg').textContent = e.message; }
    refresh();
  }}));
  return el('div', {className: 'card book'},
    el('h3', {dir: 'auto', textContent: b.title}), el('div', {className: 'au', dir: 'auto', textContent: b.author}),
    el('div', {className: 'st', dir: 'auto', textContent: status + where}),
    el('div', {className: 'bar'}, el('i', {style: 'width:' + b.progress + '%'})), row);
}
function drawBooks() {
  const box = $('#books'); box.innerHTML = '';
  let list = lib.books.filter(matches);
  if (filter === 'want') list.sort((a, b) => a.queue_position - b.queue_position);
  else list.sort((a, b) => (b.pinned - a.pinned) || a.title.localeCompare(b.title));
  if (!list.length) box.append(el('p', {className: 'mut', textContent: lib.books.length ? 'No books here yet.' : 'No books yet. Add some below.'}));
  for (const b of list) box.append(card(b));
}
async function refresh() {
  try { lib = await api('/api/library'); show(true); drawChips(); drawBooks(); }
  catch (e) {
    if (e.status === 403 || e.status === 429) { show(false); $('#msg').textContent = pin ? e.message : ''; if (pin) { pin = ''; sessionStorage.removeItem('pin'); } }
    else $('#msg').textContent = e.message;
  }
}
function send(file) {
  const li = el('li', {className: 'up'}, el('span', {className: 'n', dir: 'auto', textContent: file.name}), el('span', {className: 's', textContent: 'waiting'}), el('progress', {max: 1, value: 0}));
  $('#uploads').append(li);
  const status = li.querySelector('.s'), bar = li.querySelector('progress');
  return new Promise(done => {
    const x = new XMLHttpRequest();
    x.open('POST', '/api/upload?name=' + encodeURIComponent(file.name));
    x.setRequestHeader('X-PIN', pin);
    x.upload.onprogress = e => { if (e.lengthComputable) { bar.value = e.loaded / e.total; status.textContent = Math.round(100 * e.loaded / e.total) + '%'; } };
    x.onload = () => {
      let j = {}; try { j = JSON.parse(x.responseText); } catch (_) {}
      if (x.status === 200) { status.textContent = j.replaced ? 'replaced' : 'added'; status.className = 's good'; }
      else { status.textContent = j.error || 'failed'; status.className = 's err'; }
      bar.remove(); done();
    };
    x.onerror = () => { status.textContent = 'connection lost'; status.className = 's err'; done(); };
    x.send(file);
  });
}
async function sendAll(files) {
  $('#msg').textContent = '';
  for (const f of files) { if (!/\\.epub$/i.test(f.name)) { $('#msg').textContent = f.name + ' is not an .epub file.'; continue; } await send(f); }
  refresh();
}
$('#file').onchange = e => { sendAll([...e.target.files]); e.target.value = ''; };
const drop = $('#drop');
['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => sendAll([...e.dataTransfer.files]));
$('#go').onclick = () => { pin = $('#pin').value.trim(); sessionStorage.setItem('pin', pin); $('#msg').textContent = ''; refresh(); };
$('#pin').addEventListener('keydown', e => { if (e.key === 'Enter') $('#go').click(); });
fetch('/api/info').then(r => r.json()).then(i => { if (!i.pin_required) pin = ''; refresh(); });
</script></main></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="Add books to the e-reader over Wi-Fi (standalone)")
    ap.add_argument("--books", default="~/books", help="folder with .epub files")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--pin", default=None, help="PIN to require (default: a random one; '' for none)")
    args = ap.parse_args()
    from .state import ReadingState
    server = UploadServer(args.books, pin=args.pin, port=args.port,
                          manager=BookManager(args.books, ReadingState()))
    server.start()
    ip = local_ip() or "<this device's address>"
    print(f"Open http://{ip}:{server.port} in a browser" + (f"  (PIN: {server.pin})" if server.pin else "  (no PIN!)"), flush=True)
    print("Ctrl+C to stop.", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
