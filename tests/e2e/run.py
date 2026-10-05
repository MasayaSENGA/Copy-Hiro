#!/usr/bin/env python3
"""コピひろ (Copy-Hiro) end-to-end harness -- headless Edge + CDP, Python stdlib only.

Usage
-----
    python run.py [--ext <unpacked extension dir> | --zip <store zip>] [--emulate-activetab]
                  [--port 9401] [--http-port 9402] [--only a,b,c,d,e,f,g,h,i,j,k] [--strict]
                  [--click synthetic|cdp] [--out report.json] [--keep] [--verbose] [--list]

* --ext / --zip: what to test. Default: the runtime files of this repo (manifest.json,
                 background.js, content.js, icons/). --zip tests the store ZIP itself.
                 Either way a TEMP copy under %TEMP%\\copy-hiro-e2e is loaded.
* --port       : Edge --remote-debugging-port (default 9401)
* --http-port  : port of the built-in test page server (default port+1)
* --only       : comma separated scenario groups: a secure page / b non-secure page /
                 c pre-existing tab across chrome.runtime.reload() (+ d1 SW errors) /
                 d SW stop+restart / e restricted pages / f edge cases. c and d always run together.
               g real frame routing (same-origin / cross-origin http / cross-origin secure with and without
                 allow="clipboard-write" / srcdoc iframes; frameId from sender.frameId of a content-script ping) /
               i component with an open shadow root inside a table cell / j menu click while the page is loading /
               k pages with copy-event handlers / h frames page across chrome.runtime.reload() (runs last)
* --activetab-scope all|top : with --emulate-activetab, grant <all_urls> (default) or only http://localhost/*
                 (the top-level origin -- closer to what a real activeTab grant covers)
* --frame-click cdp|synthetic : right-click method for g/h (default: real CDP mouse input, moves focus)
* --strict     : treat "warn" (edge-case / UX findings, group f) as failures
* --click      : how the right-click is simulated. "synthetic" (default) dispatches a
                 bubbling MouseEvent("contextmenu") from the page; "cdp" uses
                 Input.dispatchMouseEvent(button=right)
* --out        : also write the JSON report to this file
* --keep       : keep the temporary Edge profile and extension copy (for debugging)

Prints a JSON report on stdout and exits 1 when any scenario is "fail"/"error"
(or "warn" with --strict). Only the Edge process tree started by this script is
killed (taskkill /T /PID <own pid>); a fresh --user-data-dir is always used.

How an action is triggered
--------------------------
The context-menu UI can not be clicked through CDP, so the harness evaluates code
in the extension service worker: when a global ``handleMenuClick(info, tab)``
exists it is called with ``info = {menuItemId, frameId: 0, pageUrl}`` and the real
``chrome.tabs.Tab``; otherwise it falls back to exactly what HEAD's onClicked
listener does: ``chrome.tabs.sendMessage(tab.id, {action})`` (not awaited).
Menu ids / action strings are in ``MENU`` below; expected clipboard + toast texts are in
expected.json (edit there when the output format changes on purpose).

Notes / limits
--------------
* Clipboard: headless Edge uses an in-memory clipboard (the user's real clipboard is not
  touched). It is read back with navigator.clipboard.readText() on a granted
  http://127.0.0.1:<http-port> page that is activated first; CRLF is normalised to LF
  (Windows stores CRLF), ``clipboard_has_crlf`` records the raw state.
* A CDP-triggered handler carries no user gesture, so an ``activeTab`` grant never exists:
  re-injection paths that rely on activeTab (instead of host_permissions) fail in group c here
  although they would work after a real context-menu click. --emulate-activetab tests a temp
  copy with host_permissions <all_urls> added to stand in for that grant.
* Group d (SW stop/restart) runs before group c: restarting a worker through CDP after
  chrome.runtime.reload() leaves the headless worker without extension bindings.
"""
import argparse
import functools
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
import zipfile

# Never write __pycache__ into the repo: Chrome refuses to load an unpacked extension
# folder that contains a name starting with "_" (the repo root is loaded in developer mode).
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import CDP, CDPError, JSError  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
PAGES = os.path.join(HERE, "pages")
# Temporary Edge profiles / extension copies live outside the repo
WORK_ROOT = os.path.join(tempfile.gettempdir(), "copy-hiro-e2e")
# Same file set as scripts/package.ps1 puts into the store ZIP
RUNTIME_FILES = ("manifest.json", "background.js", "content.js")
RUNTIME_DIRS = ("icons", "_locales")
EDGE_DEFAULT = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
NONSECURE_HOST = "copyhiro.test"
CROSS_HOST = "other.test"          # cross-site, non-secure (http)
CROSS_SECURE_HOST = "other.localhost"  # cross-site, secure context (*.localhost is potentially trustworthy)

# context-menu id (background.js) -> action string (content.js message)
MENU = {
    "copy_table": "copyTable",
    "copy_table_markdown": "copyTableMarkdown",
    "copy_all_tables": "copyAllTables",
    "copy_all_tables_markdown": "copyAllTablesMarkdown",
}
ACTIONS = list(MENU)
TOAST_ID = "table-copier-toast"


def remove_tree(path, tries=40):
    """rmtree that survives Windows MAX_PATH (Edge profiles nest deeper than 260 chars) and short file locks."""
    p = os.path.abspath(path)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    for _ in range(tries):
        try:
            shutil.rmtree(p)
            return True
        except FileNotFoundError:
            return True
        except Exception:
            time.sleep(0.25)
    shutil.rmtree(p, ignore_errors=True)
    return not os.path.exists(p)


def _port_in_use(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html; charset=utf-8",
        ".svg": "image/svg+xml",
        ".js": "text/javascript; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
    }

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/slow.html":
            # streams a table, then keeps the document "loading" for `hold` seconds (content scripts not yet injected)
            hold = float(urllib.parse.parse_qs(u.query).get("hold", ["5"])[0])
            head = ("<!doctype html><html lang='ja'><head><meta charset='utf-8'><title>slow</title></head><body>"
                    "<table id='slowt' border='1'><tr><th>遅</th><th>値</th></tr><tr><td>slow</td><td>9</td></tr></table>"
                    "<!--" + " " * 4096 + "-->").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            try:
                self.wfile.write(head)
                self.wfile.flush()
                time.sleep(hold)
                self.wfile.write(b"<p id='end'>end</p></body></html>")
                self.wfile.flush()
            except Exception:
                pass
            self.close_connection = True
            return
        return super().do_GET()

    def log_message(self, *a):
        pass


class _QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # browsers reset idle/preconnect sockets on shutdown


# --------------------------------------------------------------------------- page
OBSERVER_JS = r"""
(() => {
  if (window.__copyhiro) return 'present';
  const rec = window.__copyhiro = {toasts: []};
  const ID = %s;
  const note = (n) => {
    if (!n || n.nodeType !== 1) return;
    const el = n.id === ID ? n : (n.querySelector ? n.querySelector('#' + ID) : null);
    if (!el) return;
    let text = el.textContent;
    if (el.shadowRoot) text = el.shadowRoot.textContent;
    rec.toasts.push({text: (text || '').trim(), t: Math.round(performance.now()),
                     parent: el.parentNode ? el.parentNode.nodeName : null});
  };
  new MutationObserver(ms => { for (const m of ms) for (const n of m.addedNodes) note(n); })
    .observe(document, {childList: true, subtree: true});
  return 'installed';
})()
"""

RIGHT_CLICK_JS = r"""
((sel, fsel) => {
  let doc = document, off = {x: 0, y: 0};
  if (fsel) {
    const f = document.querySelector(fsel);
    if (!f) return {ok: false, err: 'frame not found: ' + fsel};
    f.scrollIntoView({block: 'center'});
    const fr = f.getBoundingClientRect();
    off = {x: fr.left + f.clientLeft, y: fr.top + f.clientTop};
    doc = f.contentDocument;
  }
  let el;
  if (sel.startsWith('js:')) el = (new Function('document', 'return ' + sel.slice(3)))(doc);
  else el = doc.querySelector(sel);
  if (!el) return {ok: false, err: 'element not found: ' + sel};
  if (!fsel) el.scrollIntoView({block: 'center'});
  const r = el.getBoundingClientRect();
  const x = r.left + Math.max(1, r.width / 2), y = r.top + Math.max(1, r.height / 2);
  if (%s) {
    const ev = new doc.defaultView.MouseEvent('contextmenu', {bubbles: true, cancelable: true, composed: true,
      button: 2, buttons: 2, clientX: x, clientY: y, view: doc.defaultView});
    el.dispatchEvent(ev);
  }
  return {ok: true, x: x + off.x, y: y + off.y};
})(%s, %s)
"""

TRIGGER_JS = r"""
(async (a) => {
  let tab;
  try { tab = await chrome.tabs.get(a.tabId); }
  catch (e) { return {mode: 'none', ok: false, error: 'tabs.get: ' + (e && e.message)}; }
  const h = (typeof handleMenuClick === 'function') ? handleMenuClick
          : (typeof globalThis.handleMenuClick === 'function' ? globalThis.handleMenuClick : null);
  if (h) {
    const info = {menuItemId: a.menuItemId, frameId: a.frameId, pageUrl: a.pageUrl, editable: false};
    if (a.frameUrl) info.frameUrl = a.frameUrl;  // Chrome sets frameUrl when the click was inside a subframe
    try {
      const r = await h(info, tab);
      let ret = null;
      try { ret = r === undefined ? null : JSON.parse(JSON.stringify(r)); } catch (_) { ret = String(r); }
      return {mode: 'handleMenuClick', ok: true, ret};
    } catch (e) {
      return {mode: 'handleMenuClick', ok: false, error: String(e && e.message || e)};
    }
  }
  // HEAD behaviour: onClicked -> chrome.tabs.sendMessage(tab.id, {action}) (not awaited, no catch)
  chrome.tabs.sendMessage(tab.id, {action: a.action});
  return {mode: 'sendMessage', ok: true};
})(%s)
"""

MENU_PROBE_JS = r"""
(async (ids) => {
  const r = {};
  for (const id of ids) {
    r[id] = await new Promise(res => {
      try {
        chrome.contextMenus.update(id, {}, () => {
          const e = chrome.runtime.lastError;
          res(e ? 'missing: ' + e.message : 'exists');
        });
      } catch (e) { res('error: ' + e.message); }
    });
  }
  return r;
})(%s)
"""


class Page:
    def __init__(self, h, sid, target_id, url):
        self.h, self.sid, self.target_id, self.url = h, sid, target_id, url
        self.tab_id = None

    def eval(self, expr, **kw):
        return self.h.cdp.evaluate(self.sid, expr, **kw)

    def cs_contexts(self):
        """Execution contexts of this extension's content script world (main frame only)."""
        out = []
        for ctx in list(self.h.cdp.contexts.get(self.sid, {}).values()):
            aux = ctx.get("auxData") or {}
            if aux.get("frameId") not in (None, self.target_id):
                continue
            if aux.get("type") == "isolated" and (
                    (self.h.ext_id and self.h.ext_id in ctx.get("origin", ""))
                    or ctx.get("name") == self.h.ext_name):
                out.append(ctx)
        return out

    def cs_eval(self, expr, ctx=None, **kw):
        ctxs = [ctx] if ctx else self.cs_contexts()
        if not ctxs:
            raise CDPError("no content-script context in " + self.url)
        return self.h.cdp.evaluate(self.sid, expr, context_id=ctxs[-1]["id"], **kw)

    def install_observer(self):
        return self.eval(OBSERVER_JS % json.dumps(self.h.toast_id))

    def toasts(self):
        try:
            return self.eval("window.__copyhiro ? window.__copyhiro.toasts : null") or []
        except Exception:
            return []

    def right_click(self, selector, frame=None):
        synthetic = self.h.click == "synthetic"
        r = self.eval(RIGHT_CLICK_JS % ("true" if synthetic else "false", json.dumps(selector), json.dumps(frame)))
        if not r or not r.get("ok"):
            raise CDPError(f"right_click failed: {r}")
        if not synthetic:
            for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
                p = {"type": typ, "x": r["x"], "y": r["y"], "button": "right" if typ != "mouseMoved" else "none",
                     "buttons": 2 if typ == "mousePressed" else 0, "clickCount": 1 if typ != "mouseMoved" else 0}
                self.h.cdp.send("Input.dispatchMouseEvent", p, self.sid)
            # close a native context menu if one was opened
            try:
                self.h.cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape",
                                                           "windowsVirtualKeyCode": 27}, self.sid, timeout=3)
                self.h.cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape",
                                                           "windowsVirtualKeyCode": 27}, self.sid, timeout=3)
            except Exception:
                pass
        return r

    def close(self):
        try:
            self.h.cdp.send("Target.closeTarget", {"targetId": self.target_id}, timeout=5)
        except Exception:
            pass


# ------------------------------------------------------------------------ harness
class Harness:
    def __init__(self, ext, port=9401, http_port=None, edge=EDGE_DEFAULT, workdir=None, keep=False,
                 click="synthetic", verbose=False, toast_id=TOAST_ID, extra_args=()):
        self.ext = os.path.abspath(ext)
        self.extra_args = list(extra_args)
        with open(os.path.join(self.ext, "manifest.json"), encoding="utf-8-sig") as f:
            self.manifest = json.load(f)
        self.sw_file = (self.manifest.get("background") or {}).get("service_worker", "background.js")
        self.ext_name = self.manifest.get("name")
        self.port = port
        self.http_port = http_port or port + 1
        self.edge = edge
        self.workdir = workdir or WORK_ROOT
        self.keep = keep
        self.click = click
        self.verbose = verbose
        self.toast_id = toast_id
        self.proc = None
        self.cdp = None
        self.sw = None
        self.ext_id = None
        self.httpd = None
        self.reader = None
        self.udd = None
        self.browser_version = None

    # ---------------------------------------------------------------- lifecycle
    def log(self, *a):
        if self.verbose:
            print("[harness]", *a, file=sys.stderr, flush=True)

    @property
    def secure_base(self):
        return f"http://localhost:{self.http_port}"

    @property
    def nonsecure_base(self):
        return f"http://{NONSECURE_HOST}:{self.http_port}"

    @property
    def reader_origin(self):
        return f"http://127.0.0.1:{self.http_port}"

    def start(self):
        for p in (self.port, self.http_port):
            if _port_in_use(p):
                raise RuntimeError(f"port {p} is already in use -- pick another --port/--http-port")
        handler = functools.partial(_QuietHandler, directory=PAGES)
        self.httpd = _QuietServer(("127.0.0.1", self.http_port), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        os.makedirs(self.workdir, exist_ok=True)
        self.udd = tempfile.mkdtemp(prefix="udd-", dir=self.workdir)
        cmd = [self.edge, "--headless=new", f"--remote-debugging-port={self.port}", f"--user-data-dir={self.udd}",
               f"--load-extension={self.ext}", "--no-first-run", "--no-default-browser-check",
               f"--host-resolver-rules=MAP {NONSECURE_HOST} 127.0.0.1, MAP {CROSS_HOST} 127.0.0.1, MAP {CROSS_SECURE_HOST} 127.0.0.1", "--window-size=1280,900",
               *self.extra_args, "about:blank"]
        self.log("launch", cmd)
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ver = None
        end = time.time() + 30
        while time.time() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f"Edge exited early with code {self.proc.returncode}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/version", timeout=2) as r:
                    ver = json.load(r)
                break
            except Exception:
                time.sleep(0.2)
        if not ver:
            raise RuntimeError("DevTools endpoint did not come up")
        self.browser_version = ver.get("Browser")
        self.cdp = CDP(ver["webSocketDebuggerUrl"])
        self.cdp.send("Target.setDiscoverTargets", {"discover": True})
        try:
            self.cdp.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True,
                                                   "filter": [{"type": "page"}, {"type": "service_worker"}]})
        except CDPError:
            self.cdp.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})
        self.find_sw(timeout=20)
        self.cdp.send("Browser.grantPermissions", {"origin": self.reader_origin,
                                                   "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]})
        self.reader = self.open_page(self.reader_origin + "/reader.html", wait_cs=False)
        return self

    def stop(self):
        try:
            if self.cdp:
                self.cdp.close()
        except Exception:
            pass
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                self.proc.wait(10)
            except Exception:
                pass
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.udd and not self.keep:
            remove_tree(self.udd)

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    # ------------------------------------------------------------- service worker
    def sw_sessions(self):
        return [sid for sid, s in list(self.cdp.sessions.items())
                if s["attached"] and s["type"] == "service_worker" and s["url"].startswith("chrome-extension://")
                and s["url"].endswith("/" + self.sw_file)]

    def find_sw(self, exclude=(), timeout=20):
        want_ver = self.manifest.get("version")
        name = self.ext_name or ""

        def pick():
            for sid in self.sw_sessions():
                if sid in exclude:
                    continue
                try:
                    m = self.cdp.evaluate(sid, "(() => { const m = chrome.runtime.getManifest();"
                                               " return {name: m.name, version: m.version}; })()", timeout=5)
                except Exception:
                    continue
                if m and m.get("version") == want_ver and (name.startswith("__MSG_") or m.get("name") == name):
                    if name.startswith("__MSG_"):
                        self.ext_name = m.get("name")
                    return sid
            return None

        sid = self.cdp.wait_for(pick, timeout, 0.2, what="extension service worker")
        self.sw = sid
        self.ext_id = urllib.parse.urlparse(self.cdp.sessions[sid]["url"]).hostname
        self.log("service worker", sid, self.cdp.sessions[sid]["url"])
        return sid

    def sw_eval(self, expr, timeout=15):
        try:
            return self.cdp.evaluate(self.sw, expr, timeout=timeout)
        except (JSError, CDPError) as e:
            if isinstance(e, JSError) and "chrome is not defined" not in str(e):
                raise
            # the SW session may be stale (worker restarted / replaced) -- re-discover once and retry
            self.log("sw_eval failed", repr(e), "session", self.sw, self.cdp.sessions.get(self.sw),
                     "contexts", self.cdp.contexts.get(self.sw))
            self.find_sw(timeout=10)
            return self.cdp.evaluate(self.sw, expr, timeout=timeout)

    def has_handle_menu_click(self):
        return bool(self.sw_eval("typeof handleMenuClick === 'function' || typeof globalThis.handleMenuClick === 'function'"))

    def tabs(self):
        return self.sw_eval("chrome.tabs.query({}).then(ts => ts.map(t => ({id: t.id, url: t.url || null,"
                            " active: t.active, windowId: t.windowId})))")

    def reload_extension(self, timeout=25, settle=2.0):
        """chrome.runtime.reload() in the SW, wait for the new SW. Returns SW log entries in between."""
        old = set(self.sw_sessions())
        mark = len(self.cdp.logs)
        self.cdp.send_nowait("Runtime.evaluate", {"expression": "chrome.runtime.reload()"}, sid=self.sw)
        self.find_sw(exclude=old, timeout=timeout)
        time.sleep(settle)
        return [e for e in self.cdp.logs[mark:] if e.get("target_type") == "service_worker"]

    def menu_probe(self):
        return self.sw_eval(MENU_PROBE_JS % json.dumps(list(MENU.values())))

    # --------------------------------------------------------------------- pages
    def open_page(self, url, wait_cs=True, cs_timeout=6.0):
        before = {t["id"] for t in (self.tabs() or [])} if self.sw else set()
        tid = self.cdp.send("Target.createTarget", {"url": url})["targetId"]
        sid = self.cdp.wait_for(lambda: next((s for s, v in list(self.cdp.sessions.items())
                                              if v["targetId"] == tid and v["attached"]), None),
                                10, what=f"session for {url}")
        page = Page(self, sid, tid, url)

        def loaded():
            try:
                return page.eval("document.readyState", timeout=5) == "complete"
            except Exception:
                return False

        self.cdp.wait_for(loaded, 20, 0.1, what=f"load {url}")
        if wait_cs:
            try:
                self.cdp.wait_for(page.cs_contexts, cs_timeout, 0.1, what="content script")
            except CDPError:
                pass
        if self.sw:
            def new_tab():
                ts = self.tabs() or []
                cand = [t for t in ts if t["id"] not in before]
                if not cand:
                    return None
                exact = [t for t in cand if t.get("url") and t["url"].rstrip("/") == url.rstrip("/")]
                return (exact or sorted(cand, key=lambda t: t["id"]))[-1]["id"]
            try:
                page.tab_id = self.cdp.wait_for(new_tab, 5, 0.1, what="tab id")
            except CDPError:
                page.tab_id = None
        try:
            page.install_observer()
        except Exception:
            pass
        return page

    # ----------------------------------------------------------------- clipboard
    def activate(self, page):
        """Make `page` the active tab (async clipboard reads only work in the active tab)."""
        self.cdp.send("Target.activateTarget", {"targetId": page.target_id})
        try:
            self.cdp.wait_for(lambda: page.eval("document.visibilityState", timeout=5) == "visible", 3, 0.05,
                              what="tab visible")
        except Exception:
            pass

    def set_clipboard(self, text):
        self.activate(self.reader)
        return self.reader.eval(f"navigator.clipboard.writeText({json.dumps(text)}).then(() => true)")

    def read_clipboard(self):
        self.activate(self.reader)
        return self.reader.eval("navigator.clipboard.readText()")

    # -------------------------------------------------------------------- action
    def trigger(self, page, action, frame_id=0, frame_url=None):
        args = {"tabId": page.tab_id, "menuItemId": MENU[action], "action": action, "frameId": frame_id,
                "pageUrl": page.url, "frameUrl": frame_url}
        return self.sw_eval(TRIGGER_JS % json.dumps(args))

    def act(self, page, action, wait=3.0, settle=0.6):
        try:
            page.install_observer()
        except Exception:
            pass
        sentinel = f"SENTINEL-{uuid.uuid4().hex[:8]}"
        self.set_clipboard(sentinel)
        self.activate(page)
        n0 = len(page.toasts())
        mark = len(self.cdp.logs)
        t0 = time.time()
        res = self.trigger(page, action)
        end = t0 + wait
        while time.time() < end:
            if len(page.toasts()) > n0:
                break
            time.sleep(0.05)
        time.sleep(settle)
        toasts = page.toasts()[n0:]
        raw = self.read_clipboard()
        clip = raw.replace("\r\n", "\n") if isinstance(raw, str) else raw  # Windows clipboard stores CRLF
        logs = self.cdp.logs[mark:]
        return {
            "action": action,
            "trigger": res,
            "toasts": [t["text"] for t in toasts],
            "toast_parents": [t.get("parent") for t in toasts],
            "clipboard": clip,
            "clipboard_unchanged": clip == sentinel,
            "clipboard_has_crlf": isinstance(raw, str) and "\r\n" in raw,
            "sw_errors": [_short(e) for e in logs if e.get("target_type") == "service_worker" and _is_err(e)],
            "sw_warnings": [_short(e) for e in logs if e.get("target_type") == "service_worker" and _is_warn(e)],
            "page_errors": [_short(e) for e in logs if e.get("sid") == page.sid and _is_err(e)],
            "elapsed_ms": int((time.time() - t0) * 1000),
        }


def _is_err(e):
    return e.get("kind") == "exception" or e.get("level") in ("error", "assert")


def _is_warn(e):
    return e.get("level") in ("warning", "warn")


def _short(e):
    w = e.get("world") or {}
    return {"kind": e.get("kind"), "level": e.get("level"), "text": (e.get("text") or "")[:400],
            "world": w.get("type"), "url": e.get("url")}


# ---------------------------------------------------------------------- scenarios
class Ctx:
    def __init__(self, h, expected):
        self.h = h
        self.E = expected
        self.results = []
        self.pages = {}

    def exp_text(self, table, fmt):
        return self.E["tables"][table][fmt]

    def exp_all(self, fmt):
        sep = self.E["all_tables_separator"]
        return sep.join(self.E["tables"][t][fmt] for t in self.E["all_tables_order"])

    def toast(self, key, n=None):
        s = self.E["toasts"][key]
        return s.replace("{n}", str(n)) if n is not None else s


def _check_copy(r, exp_clip, exp_toast):
    problems = []
    if exp_clip is None:
        if not r["clipboard_unchanged"]:
            problems.append(f"clipboard changed unexpectedly: {r['clipboard']!r}")
    elif r["clipboard"] != exp_clip:
        problems.append("clipboard mismatch" + (" (unchanged / nothing copied)" if r["clipboard_unchanged"] else ""))
    if exp_toast is not None:
        if not r["toasts"]:
            problems.append(f"no toast (expected {exp_toast!r})")
        elif r["toasts"][-1] != exp_toast:
            problems.append(f"toast {r['toasts'][-1]!r} != {exp_toast!r}")
    if len(r["toasts"]) > 1:
        problems.append(f"{len(r['toasts'])} toasts (handled more than once?)")
    if r["sw_errors"]:
        problems.append("service-worker error: " + r["sw_errors"][0]["text"][:160])
    if r["page_errors"]:
        problems.append("content-script/page error: " + r["page_errors"][0]["text"][:160])
    if r["trigger"] and not r["trigger"].get("ok"):
        problems.append("handler rejected: " + str(r["trigger"].get("error")))
    return problems


def _record(c, name, group, status, summary, **details):
    c.results.append({"name": name, "group": group, "status": status, "summary": summary, **details})


def scenario_a(c):
    """(a) secure context (http://localhost) -- all 4 actions + selection / not-found cases."""
    h = c.h
    p = h.open_page(h.secure_base + "/tables.html")
    env = p.eval("({secure: isSecureContext, clipboard: typeof navigator.clipboard})")
    cases = [
        ("a1_copy_table", "#sales tbody tr:nth-child(1) td:nth-child(2)", "copy_table", c.exp_text("sales", "tsv"), c.toast("copy_table")),
        ("a2_copy_table_markdown", "#sales thead th:nth-child(1)", "copy_table_markdown", c.exp_text("sales", "md"), c.toast("copy_table_markdown")),
        ("a3_copy_all_tables", "#outside", "copy_all_tables", c.exp_all("tsv"), c.toast("copy_all_tables", 3)),
        ("a4_copy_all_tables_markdown", "#outside", "copy_all_tables_markdown", c.exp_all("md"), c.toast("copy_all_tables_markdown", 3)),
        ("a5_copy_second_table", "#simple td", "copy_table", c.exp_text("simple", "tsv"), c.toast("copy_table")),
        ("a6_multiline_markdown", "#multi tr:nth-child(2) td:nth-child(2) div", "copy_table_markdown", c.exp_text("multi", "md"), c.toast("copy_table_markdown")),
        ("a7_outside_table", "#outside", "copy_table", None, c.toast("not_found")),
        ("a7b_outside_table_markdown", "#outside", "copy_table_markdown", None, c.toast("not_found")),
    ]
    for name, sel, action, exp_clip, exp_toast in cases:
        p.right_click(sel)
        r = h.act(p, action)
        probs = _check_copy(r, exp_clip, exp_toast)
        _record(c, name, "a", "fail" if probs else "pass", "; ".join(probs) or "ok", env=env,
                selector=sel, result=r, expected_clipboard=exp_clip)
    q = h.open_page(h.secure_base + "/notable.html")
    for action in ACTIONS:
        q.right_click("#para")
        r = h.act(q, action)
        probs = _check_copy(r, None, c.toast("not_found"))
        _record(c, f"a8_no_table_page_{action}", "a", "fail" if probs else "pass", "; ".join(probs) or "ok", result=r)
    q.close()
    c.pages["secure"] = p


def scenario_b(c):
    """(b) non-secure context (http://copyhiro.test) -- navigator.clipboard is undefined there."""
    h = c.h
    p = h.open_page(h.nonsecure_base + "/tables.html")
    env = p.eval("({secure: isSecureContext, clipboard: typeof navigator.clipboard, href: location.href})")
    cs = bool(p.cs_contexts())
    cs_env = None
    if cs:
        try:
            cs_env = p.cs_eval("({secure: isSecureContext, clipboard: typeof navigator.clipboard})")
        except Exception as e:
            cs_env = str(e)
    for name, sel, action, exp_clip, exp_toast in [
        ("b1_nonsecure_copy_table", "#sales tbody td", "copy_table", c.exp_text("sales", "tsv"), c.toast("copy_table")),
        ("b2_nonsecure_copy_all_markdown", "#outside", "copy_all_tables_markdown", c.exp_all("md"), c.toast("copy_all_tables_markdown", 3)),
    ]:
        p.right_click(sel)
        r = h.act(p, action)
        probs = _check_copy(r, exp_clip, exp_toast)
        if env.get("secure"):
            probs.insert(0, "precondition: page unexpectedly is a secure context")
        _record(c, name, "b", "fail" if probs else "pass", "; ".join(probs) or "ok", env=env,
                content_script_env=cs_env, result=r)
    p.close()


def scenario_c_d(c):
    """(c) tab opened before the extension (re)loads, (d) SW errors across reload / SW restart."""
    h = c.h
    p = h.open_page(h.secure_base + "/tables.html")
    p2 = h.open_page(h.secure_base + "/tables.html?second")
    p.right_click("#sales td")
    old_ctx = [x["id"] for x in p.cs_contexts()]
    menus_before = h.menu_probe()
    sw_logs = h.reload_extension()
    menus_after = h.menu_probe()
    errs = [_short(e) for e in sw_logs if _is_err(e)]
    probs = []
    if errs:
        probs.append(f"{len(errs)} SW error(s) during reload: " + errs[0]["text"][:200])
    missing = {k: v for k, v in (menus_after or {}).items() if v != "exists"}
    if missing:
        probs.append(f"context menu items missing after reload: {missing}")
    _record(c, "d1_sw_errors_across_reload", "d", "fail" if probs else "pass", "; ".join(probs) or "ok",
            sw_log=[_short(e) for e in sw_logs], menus_before=menus_before, menus_after=menus_after)

    # orphaned content-script state in the old tab
    orphan = []
    for ctx in p.cs_contexts():
        try:
            v = p.cs_eval("({runtimeId: (typeof chrome !== 'undefined' && chrome.runtime) ? (chrome.runtime.id || null) : null,"
                          " lastElement: typeof lastElement})", ctx=ctx)
        except Exception as e:
            v = str(e)
        orphan.append({"ctx": ctx["id"], "was_present_before_reload": ctx["id"] in old_ctx, "state": v})

    # c1: copy all tables (no element needed) in the pre-existing tab
    r1 = h.act(p, "copy_all_tables")
    probs1 = _check_copy(r1, c.exp_all("tsv"), c.toast("copy_all_tables", 3))
    silent = r1["clipboard_unchanged"] and not r1["toasts"]
    _record(c, "c1_preexisting_tab_copy_all_after_reload", "c", "fail" if probs1 else "pass",
            ("SILENT NO-OP: " if silent else "") + ("; ".join(probs1) or "ok"), orphan_contexts=orphan, result=r1)

    # c2: a second pre-existing tab whose FIRST action after the update is "copy this table"
    #     (the user right-clicks after the update; only the orphaned script sees that right-click)
    p2.right_click("#sales td")
    r2 = h.act(p2, "copy_table")
    probs2 = _check_copy(r2, c.exp_text("sales", "tsv"), c.toast("copy_table"))
    silent2 = r2["clipboard_unchanged"] and not r2["toasts"]
    if probs2 and r2["toasts"] and r2["clipboard_unchanged"] and not r2["sw_errors"] and not r2["page_errors"] \
            and len(r2["toasts"]) == 1:
        st, summ = "warn", f"not copied but user got feedback: {r2['toasts'][-1]!r}"
    else:
        st, summ = ("fail" if probs2 else "pass"), ("SILENT NO-OP: " if silent2 else "") + ("; ".join(probs2) or "ok")
    _record(c, "c2_preexisting_tab_first_action_copy_table", "c", st, summ, result=r2)

    # c2b: same tab, the user right-clicks once more and retries
    p2.right_click("#sales td")
    r2b = h.act(p2, "copy_table")
    probs2b = _check_copy(r2b, c.exp_text("sales", "tsv"), c.toast("copy_table"))
    silent2b = r2b["clipboard_unchanged"] and not r2b["toasts"]
    _record(c, "c2b_preexisting_tab_retry_copy_table", "c", "fail" if probs2b else "pass",
            ("SILENT NO-OP: " if silent2b else "") + ("; ".join(probs2b) or "ok"), result=r2b)

    # c2c: same tab, markdown variant (checks there is still exactly one live handler -> one toast)
    p2.right_click("#simple td")
    r2c = h.act(p2, "copy_table_markdown")
    probs2c = _check_copy(r2c, c.exp_text("simple", "md"), c.toast("copy_table_markdown"))
    _record(c, "c2c_preexisting_tab_markdown_single_handler", "c", "fail" if probs2c else "pass",
            "; ".join(probs2c) or "ok", result=r2c)
    p2.close()

    # c3: a tab opened after the reload must work normally
    q = h.open_page(h.secure_base + "/tables.html")
    q.right_click("#simple td")
    r3 = h.act(q, "copy_table")
    probs3 = _check_copy(r3, c.exp_text("simple", "tsv"), c.toast("copy_table"))
    _record(c, "c3_new_tab_after_reload", "c", "fail" if probs3 else "pass", "; ".join(probs3) or "ok", result=r3)
    q.close()

    p.close()


def scenario_d2(c):
    """(d2) SW restart (idle termination): stop the worker, wake it with runtime.sendMessage from a content script."""
    h = c.h
    sw_url = h.cdp.sessions[h.sw]["url"]
    q = h.open_page(h.secure_base + "/notable.html")  # opened while the SW is alive (open_page queries tabs)
    sw_sid = h.sw
    mark_ev = len(h.cdp.events)
    mark = len(h.cdp.logs)
    h.cdp.send("ServiceWorker.enable", sid=h.reader.sid)
    h.cdp.send("ServiceWorker.stopAllWorkers", sid=h.reader.sid)

    def stopped():
        for e in h.cdp.events[mark_ev:]:
            if e.get("method") == "ServiceWorker.workerVersionUpdated":
                for v in e["params"].get("versions", []):
                    if v.get("scriptURL") == sw_url and v.get("runningStatus") == "stopped":
                        return True
        return False

    try:
        h.cdp.wait_for(stopped, 10, 0.1, what="SW stopped")
    except CDPError:
        _record(c, "d2_sw_restart", "d", "info", "could not stop the service worker via ServiceWorker.stopAllWorkers")
        q.close()
        return
    mark_ev = len(h.cdp.events)
    try:
        wake = q.cs_eval("chrome.runtime.sendMessage({__harness: 'wake'}).then(r => 'resolved:' + JSON.stringify(r),"
                         " e => 'rejected:' + e.message)")
    except Exception as e:
        wake = "error: " + str(e)

    def restarted():
        if any(e.get("method") == "Runtime.executionContextCreated" and e.get("sessionId") in h.sw_sessions()
               for e in h.cdp.events[mark_ev:]):
            return True
        return False

    try:
        h.cdp.wait_for(restarted, 15, 0.1, what="SW restart")
        for sid in h.sw_sessions():  # same session is reused after a restart, but be tolerant
            try:
                h.cdp.evaluate(sid, "1", timeout=3)
                h.sw = sid
                break
            except Exception:
                continue
    except CDPError:
        _record(c, "d2_sw_restart", "d", "error", f"service worker did not restart (wake={wake})")
        q.close()
        return
    time.sleep(1.5)
    logs = [e for e in h.cdp.logs[mark:] if e.get("target_type") == "service_worker" and e.get("sid") == sw_sid
            or (e.get("target_type") == "service_worker" and e.get("sid") == h.sw)]
    errs = [_short(e) for e in logs if _is_err(e)]
    menus = h.menu_probe()
    missing = {k: v for k, v in (menus or {}).items() if v != "exists"}
    probs = []
    if errs:
        probs.append(f"{len(errs)} SW error(s) on restart: " + errs[0]["text"][:200])
    if missing:
        probs.append(f"menu items missing after restart: {missing}")
    # the extension must still work after the restart
    q2 = h.open_page(h.secure_base + "/tables.html")
    q2.right_click("#simple td")
    r = h.act(q2, "copy_table")
    p3 = _check_copy(r, c.exp_text("simple", "tsv"), c.toast("copy_table"))
    probs += ["after restart: " + x for x in p3]
    _record(c, "d2_sw_restart", "d", "fail" if probs else "pass", "; ".join(probs) or "ok",
            wake=wake, sw_log=[_short(e) for e in logs], menus_after=menus, result=r)
    q2.close()
    q.close()


def scenario_e(c):
    """(e) restricted pages -- no content script; the click must not cause an uncaught error."""
    h = c.h
    for name, url in [("e1_about_blank", "about:blank"), ("e2_edge_version", "edge://version")]:
        p = h.open_page(url, wait_cs=False)
        r = h.act(p, "copy_table", wait=1.5)
        probs = []
        if r["sw_errors"]:
            probs.append("uncaught SW error: " + r["sw_errors"][0]["text"][:200])
        if r["trigger"] and not r["trigger"].get("ok"):
            probs.append("handler rejected: " + str(r["trigger"].get("error")))
        if not r["clipboard_unchanged"]:
            probs.append("clipboard changed")
        _record(c, name, "e", "fail" if probs else "pass", "; ".join(probs) or "ok (no uncaught error)",
                content_script=bool(p.cs_contexts()), result=r)
        p.close()


def scenario_f(c):
    """(f) edge cases (non-fatal 'warn' unless --strict)."""
    h = c.h
    T = c.toast

    # f1 iframe: right-click outer table, then a table inside a same-origin iframe -> "copy this table"
    p = h.open_page(h.secure_base + "/iframe.html")
    p.eval("new Promise(r => { const f = document.getElementById('frame');"
           " if (f.contentDocument && f.contentDocument.readyState === 'complete') r(1); else f.onload = () => r(1); })")
    p.right_click("#outer td")
    p.right_click("#inner td", frame="#frame")
    # route like Chrome does: info.frameId = the iframe's extension frameId (sender.frameId of its content script)
    from frames_ext import SW_FRAME_RECORDER_JS
    fid = 0
    try:
        h.sw_eval(SW_FRAME_RECORDER_JS)
        ftag = "f1" + uuid.uuid4().hex[:6]
        child = [x for x in h.cdp.contexts.get(p.sid, {}).values()
                 if (x.get("auxData") or {}).get("type") == "isolated" and h.ext_id in x.get("origin", "")
                 and (x.get("auxData") or {}).get("frameId") != p.target_id]
        h.cdp.evaluate(p.sid, "chrome.runtime.sendMessage({__harness: 'frameid', name: 'frame', tag: %s}).then(() => 1, e => 0)"
                       % json.dumps(ftag), context_id=child[-1]["id"])
        time.sleep(0.3)
        fid = next(x["frameId"] for x in h.sw_eval("globalThis.__hf || []") if x.get("tag") == ftag)
    except Exception as e:
        h.log("f1 frame id lookup failed", e)
    mark = len(h.cdp.logs)
    sentinel = "SENTINEL-f1"
    h.set_clipboard(sentinel)
    h.activate(p)
    res = h.trigger(p, "copy_table", fid)
    time.sleep(1.2)
    raw = h.read_clipboard()
    frame_toasts = []
    try:
        frame_toasts = p.eval("(() => { const d = document.getElementById('frame').contentDocument;"
                              " const t = d.getElementById(%s); return t ? [t.textContent] : []; })()" % json.dumps(h.toast_id))
    except Exception:
        pass
    r = {"action": "copy_table", "trigger": res, "frame_id_sent": fid, "toasts": frame_toasts,
         "top_toasts": [t["text"] for t in p.toasts()], "clipboard": raw.replace("\r\n", "\n") if isinstance(raw, str) else raw}
    r["clipboard_unchanged"] = r["clipboard"] == sentinel
    inner, outer = "内側\t値\ninner\t2", "外側\t値\nouter\t1"
    if r["clipboard"] == inner:
        st, s = "pass", "inner (iframe) table copied"
    elif r["clipboard"] == outer:
        st, s = "warn", "STALE: copied the previously right-clicked OUTER table although the user right-clicked inside the iframe"
    elif r["toasts"]:
        st, s = "warn", f"iframe table not supported; toast {r['toasts'][-1]!r}"
    else:
        st, s = "warn", "nothing happened"
    _record(c, "f1_iframe_stale_target", "f", st, s, result=r)
    p.close()

    # edge.html
    p = h.open_page(h.secure_base + "/edge.html")
    p.right_click("#span td")
    r = h.act(p, "copy_table")
    rows = [ln.split("\t") for ln in r["clipboard"].split("\n")] if not r["clipboard_unchanged"] else []
    widths = [len(x) for x in rows]
    st = "pass" if widths and all(w == 3 for w in widths) else "warn"
    _record(c, "f2_colspan_rowspan", "f", st, f"column counts per row {widths} (want all 3)", result=r)

    p.right_click("#nested > tbody > tr:nth-child(2) > td:nth-child(1)")
    r = h.act(p, "copy_table")
    rows = [ln.split("\t") for ln in r["clipboard"].split("\n")] if not r["clipboard_unchanged"] else []
    widths = [len(x) for x in rows]
    st = "pass" if widths == [2, 2] else "warn"
    _record(c, "f3_nested_table_tsv", "f", st, f"outer table rows/columns {widths} (want [2, 2])", result=r)

    p.right_click("#empty")
    r = h.act(p, "copy_table")
    if r["clipboard_unchanged"] and r["toasts"]:
        st, s = "pass", f"empty table not copied, toast {r['toasts'][-1]!r}"
    else:
        st, s = "warn", f"empty table: clipboard={r['clipboard']!r} toast={r['toasts']}"
    _record(c, "f4_empty_table", "f", st, s, result=r)

    r = h.act(p, "copy_all_tables")
    clip = r["clipboard"] or ""
    hidden = "secret" in clip
    nested_twice = clip.count("a\tb") > 1
    _record(c, "f5_hidden_and_nested_in_copy_all", "f", "info",
            f"toast={r['toasts']}; hidden (display:none) table included={hidden}; "
            f"nested table also copied as a separate table={nested_twice}", result=r)

    p.right_click("#special td")
    r = h.act(p, "copy_table")
    rows = [ln.split("\t") for ln in r["clipboard"].split("\n")] if not r["clipboard_unchanged"] else []
    widths = [len(x) for x in rows]
    st = "pass" if widths and all(w == 2 for w in widths) else "warn"
    _record(c, "f6_tab_char_in_cell", "f", st, f"column counts per row {widths} (want all 2)", result=r)
    p.close()

    # f7 frameset: toast is appended to <frameset> -> invisible
    p = h.open_page(h.secure_base + "/frameset.html")
    r = h.act(p, "copy_all_tables")
    vis = None
    try:
        vis = p.eval(f"(() => {{ const t = document.getElementById({json.dumps(h.toast_id)}); if (!t) return null;"
                     " const b = t.getBoundingClientRect(); return {w: b.width, h: b.height, parent: t.parentNode.nodeName}; })()")
    except Exception:
        pass
    st = "pass" if (vis and vis["w"] > 0) or not r["toasts"] and not r["page_errors"] else "warn"
    _record(c, "f7_frameset_toast_visibility", "f", st, f"toasts={r['toasts']} rect={vis}", result=r)
    p.close()

    # f8 SVG document: document.body is null
    p = h.open_page(h.secure_base + "/image.svg")
    cs = bool(p.cs_contexts())
    r = h.act(p, "copy_all_tables", wait=1.5)
    st = "pass" if not r["page_errors"] and not r["sw_errors"] else "warn"
    _record(c, "f8_svg_document_no_body", "f", st,
            f"content script injected={cs}; errors={[e['text'][:120] for e in r['page_errors'] + r['sw_errors']]}",
            result=r)
    p.close()

    # f9 shadow DOM table
    p = h.open_page(h.secure_base + "/shadow.html")
    p.right_click("js:document.querySelector('#host').shadowRoot.querySelector('td')")
    r = h.act(p, "copy_table")
    st = "pass" if r["clipboard"] == "影\t値\nshadow\t3" else "warn"
    _record(c, "f9_shadow_dom_table", "f", st, f"toast={r['toasts']} clipboard_unchanged={r['clipboard_unchanged']}",
            result=r)
    p.close()


# d2 (SW stop/restart) runs before c (extension reload): restarting a worker via CDP after
# chrome.runtime.reload() leaves the headless worker without extension bindings (harness artefact).
from frames_ext import scenario_g, scenario_h, scenario_i, scenario_j, scenario_k  # noqa: E402

# h reloads the extension again, so it runs last.
GROUPS = [("a", scenario_a), ("b", scenario_b), ("d", scenario_d2), ("c", scenario_c_d), ("e", scenario_e),
          ("f", scenario_f), ("g", scenario_g), ("i", scenario_i), ("j", scenario_j), ("k", scenario_k),
          ("h", scenario_h)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ext", required=False,
                    help="unpacked extension directory (default: the runtime files of this repo)")
    ap.add_argument("--zip", default=None,
                    help="test this store ZIP instead (e.g. dist/copy-hiro-v2.4.zip from scripts/package.ps1)")
    ap.add_argument("--port", type=int, default=9401, help="remote debugging port (default 9401)")
    ap.add_argument("--http-port", type=int, default=None, help="test page server port (default port+1)")
    ap.add_argument("--edge", default=EDGE_DEFAULT)
    ap.add_argument("--only", default="", help="comma separated groups (a,b,c,d,e,f,g,h,i,j); c and d run together")
    ap.add_argument("--strict", action="store_true", help="warn counts as failure")
    ap.add_argument("--click", choices=["synthetic", "cdp"], default="synthetic")
    ap.add_argument("--expected", default=os.path.join(HERE, "expected.json"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--list", action="store_true", help="list scenario groups and exit")
    ap.add_argument("--activetab-scope", choices=["all", "top"], default="all",
                    help="with --emulate-activetab: 'all' adds host_permissions <all_urls>; 'top' adds only "
                         "http://localhost/* (the top-level origin of the test pages) to model activeTab more faithfully")
    ap.add_argument("--frame-click", choices=["cdp", "synthetic"], default="cdp",
                    help="how frame scenarios (g/h) right-click: real CDP mouse input (default) or synthetic event")
    ap.add_argument("--emulate-activetab", action="store_true",
                    help="test a TEMP copy with host_permissions <all_urls> added, to stand in for the activeTab "
                         "grant a real context-menu click gives (CDP-triggered clicks carry none)")
    a = ap.parse_args(argv)
    if a.list:
        for g, f in GROUPS:
            print(g, "-", (f.__doc__ or "").strip())
        return 0
    if a.ext and a.zip:
        ap.error("--ext and --zip can not be used together")
    with open(a.expected, encoding="utf-8") as f:
        expected = json.load(f)
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    # Always test a TEMP copy, so the source folder is never modified and the repo stays clean
    os.makedirs(WORK_ROOT, exist_ok=True)
    tmp_copy = tempfile.mkdtemp(prefix="ext-", dir=WORK_ROOT)
    dst = os.path.join(tmp_copy, "ext")
    if a.zip:
        source = os.path.abspath(a.zip)
        with zipfile.ZipFile(source) as z:
            z.extractall(dst)
    elif a.ext:
        source = os.path.abspath(a.ext)
        shutil.copytree(source, dst, ignore=shutil.ignore_patterns(".git", ".claude", "node_modules", "__pycache__"))
    else:
        source = REPO_ROOT
        os.makedirs(dst)
        for name in RUNTIME_FILES:
            shutil.copy2(os.path.join(REPO_ROOT, name), dst)
        for name in RUNTIME_DIRS:
            if os.path.isdir(os.path.join(REPO_ROOT, name)):
                shutil.copytree(os.path.join(REPO_ROOT, name), os.path.join(dst, name))
    ext_dir = dst
    if a.emulate_activetab:
        # activeTab is only granted by a real menu click; emulate it with host_permissions on the TEMP copy
        mp = os.path.join(dst, "manifest.json")
        with open(mp, encoding="utf-8-sig") as f:
            m = json.load(f)
        extra = {"<all_urls>"} if a.activetab_scope == "all" else {"http://localhost/*"}
        m["host_permissions"] = sorted(set(m.get("host_permissions", [])) | extra)
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
    h = Harness(ext_dir, a.port, a.http_port, a.edge, keep=a.keep, click=a.click, verbose=a.verbose)
    c = Ctx(h, expected)
    c.frame_click = a.frame_click
    c.activetab_scope = a.activetab_scope if a.emulate_activetab else "none"
    report = {"harness": {}, "scenarios": c.results}
    t0 = time.time()
    try:
        h.start()
        report["harness"] = {
            "browser": h.browser_version, "extension_dir": source, "extension_id": h.ext_id,
            "extension_version": h.manifest.get("version"), "service_worker": h.cdp.sessions[h.sw]["url"],
            "trigger_mode": "handleMenuClick" if h.has_handle_menu_click() else "sendMessage (HEAD fallback)",
            "click": h.click, "secure_base": h.secure_base, "nonsecure_base": h.nonsecure_base,
            "emulate_activetab": bool(a.emulate_activetab), "activetab_scope": c.activetab_scope,
            "frame_click": a.frame_click,
            "user_data_dir": h.udd if a.keep else "(deleted)",
        }
        for g, fn in GROUPS:
            if only and g not in only and not (g == "c" and "d" in only) and not (g == "d" and "c" in only):
                continue
            try:
                fn(c)
            except Exception as e:
                c.results.append({"name": f"group_{g}", "group": g, "status": "error",
                                  "summary": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()})
    except Exception as e:
        c.results.append({"name": "harness_start", "group": "-", "status": "error",
                          "summary": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()})
    finally:
        h.stop()
        if tmp_copy and not a.keep:
            remove_tree(tmp_copy)
    counts = {}
    for r in c.results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    bad = {"fail", "error"} | ({"warn"} if a.strict else set())
    failed = [r["name"] for r in c.results if r["status"] in bad]
    report["summary"] = {"counts": counts, "failed": failed, "ok": not failed, "seconds": round(time.time() - t0, 1)}
    out = json.dumps(report, ensure_ascii=False, indent=2)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(out)
    sys.stdout.buffer.write(out.encode("utf-8") + b"\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
