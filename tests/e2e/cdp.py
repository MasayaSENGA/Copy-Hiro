"""Minimal WebSocket + Chrome DevTools Protocol client (Python stdlib only).

Used by run.py / experiments.py. One WebSocket to the *browser* endpoint, flat
sessions (Target.setAutoAttach flatten=true), a reader thread that resolves
command responses and records events / console output for every attached
target (pages and service workers).
"""
import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
import urllib.parse

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class CDPError(Exception):
    pass


class JSError(Exception):
    """Exception thrown by evaluated JavaScript."""

    def __init__(self, text, details=None):
        super().__init__(text)
        self.text = text
        self.details = details or {}


def _mask(data, key):
    if not data:
        return b""
    n = len(data)
    m = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(m, "big")).to_bytes(n, "big")


class WebSocket:
    def __init__(self, url, timeout=10):
        u = urllib.parse.urlparse(url)
        host, port = u.hostname, u.port or 80
        path = u.path + ("?" + u.query if u.query else "")
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        self.buf = bytearray()
        while b"\r\n\r\n" not in self.buf:
            d = self.sock.recv(4096)
            if not d:
                raise ConnectionError("websocket handshake: connection closed")
            self.buf += d
        head, _, rest = bytes(self.buf).partition(b"\r\n\r\n")
        self.buf = bytearray(rest)
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise ConnectionError("websocket handshake failed: " + lines[0])
        accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
        hdrs = {ln.split(":", 1)[0].strip().lower(): ln.split(":", 1)[1].strip() for ln in lines[1:] if ":" in ln}
        if hdrs.get("sec-websocket-accept") != accept:
            raise ConnectionError("websocket handshake: bad Sec-WebSocket-Accept")
        self.sock.settimeout(None)
        self.send_lock = threading.Lock()
        self.closed = False

    def _read_exact(self, n):
        while len(self.buf) < n:
            d = self.sock.recv(1 << 16)
            if not d:
                raise ConnectionError("websocket closed")
            self.buf += d
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def _send_frame(self, opcode, payload):
        hdr = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        key = os.urandom(4)
        hdr += key
        with self.send_lock:
            self.sock.sendall(bytes(hdr) + _mask(payload, key))

    def send(self, text):
        self._send_frame(0x1, text.encode("utf-8"))

    def recv(self):
        msg = bytearray()
        while True:
            b1, b2 = self._read_exact(2)
            fin, op = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read_exact(8))[0]
            key = self._read_exact(4) if b2 & 0x80 else None
            data = self._read_exact(n)
            if key:
                data = _mask(data, key)
            if op == 0x8:
                self.closed = True
                return None
            if op == 0x9:
                self._send_frame(0xA, data)
                continue
            if op == 0xA:
                continue
            if op in (0x1, 0x2):
                msg = bytearray(data)
            else:
                msg += data
            if fin:
                return bytes(msg).decode("utf-8", "replace")

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class CDP:
    """Browser-level CDP connection with flat auto-attached sessions."""

    def __init__(self, ws_url):
        self.ws = WebSocket(ws_url)
        self.lock = threading.Lock()
        self.cv = threading.Condition()
        self.next_id = 0
        self.pending = {}
        self.events = []
        self.sessions = {}   # sessionId -> {targetId, type, url, attached, t}
        self.contexts = {}   # sessionId -> {ctxId: context}
        self.logs = []       # console / exception / Log entries of every session
        self.dead = None
        self.attach_oopif = True
        self.reader = threading.Thread(target=self._loop, daemon=True)
        self.reader.start()

    # ------------------------------------------------------------------ io
    def _loop(self):
        while True:
            try:
                raw = self.ws.recv()
            except Exception as e:  # connection gone
                self.dead = e
                break
            if raw is None:
                self.dead = ConnectionError("closed by peer")
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if "id" in msg:
                with self.lock:
                    p = self.pending.pop(msg["id"], None)
                if p:
                    p[1] = msg
                    p[0].set()
                continue
            msg["t"] = time.time()
            try:
                self._on_event(msg)
            except Exception as e:  # never let the reader die
                self.logs.append({"kind": "harness", "level": "error", "text": f"event handler: {e!r}"})
            with self.cv:
                self.events.append(msg)
                self.cv.notify_all()
        with self.lock:
            for p in self.pending.values():
                p[1] = {"error": {"message": "connection closed"}}
                p[0].set()
            self.pending.clear()

    def send_nowait(self, method, params=None, sid=None):
        with self.lock:
            self.next_id += 1
            mid = self.next_id
            slot = [threading.Event(), None]
            self.pending[mid] = slot
        m = {"id": mid, "method": method, "params": params or {}}
        if sid:
            m["sessionId"] = sid
        self.ws.send(json.dumps(m))
        return mid, slot

    def send(self, method, params=None, sid=None, timeout=30):
        mid, slot = self.send_nowait(method, params, sid)
        if not slot[0].wait(timeout):
            with self.lock:
                self.pending.pop(mid, None)
            raise CDPError(f"timeout waiting for {method}")
        r = slot[1]
        if "error" in r:
            e = r["error"]
            raise CDPError(f"{method}: {e.get('message')} {e.get('data', '')}".strip())
        return r.get("result", {})

    # -------------------------------------------------------------- events
    def _on_event(self, msg):
        m = msg.get("method")
        p = msg.get("params", {})
        sid = msg.get("sessionId")
        if m == "Target.attachedToTarget":
            info = p["targetInfo"]
            s = p["sessionId"]
            self.sessions[s] = {"targetId": info["targetId"], "type": info["type"], "url": info.get("url", ""),
                                "title": info.get("title", ""), "attached": True, "t": time.time()}
            self.contexts.setdefault(s, {})
            t = info["type"]
            self.sessions[s]["parent_sid"] = sid  # None for browser-level auto-attach, page sid for OOPIFs
            if t in ("page", "iframe", "service_worker", "worker", "shared_worker"):
                self.send_nowait("Runtime.enable", sid=s)
                self.send_nowait("Log.enable", sid=s)
                if t in ("page", "iframe"):
                    self.send_nowait("Page.enable", sid=s)
                    self.send_nowait("Emulation.setFocusEmulationEnabled", {"enabled": True}, sid=s)
                    if self.attach_oopif:
                        # out-of-process (cross-site) iframes are separate targets: attach them too
                        self.send_nowait("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True,
                                                                  "flatten": True}, sid=s)
            if p.get("waitingForDebugger"):
                self.send_nowait("Runtime.runIfWaitingForDebugger", sid=s)
        elif m == "Target.detachedFromTarget":
            s = p.get("sessionId")
            if s in self.sessions:
                self.sessions[s]["attached"] = False
        elif m == "Target.targetInfoChanged":
            info = p["targetInfo"]
            for s in self.sessions.values():
                if s["targetId"] == info["targetId"]:
                    s["url"] = info.get("url", s["url"])
                    s["title"] = info.get("title", s["title"])
        elif m == "Runtime.executionContextCreated":
            ctx = p["context"]
            self.contexts.setdefault(sid, {})[ctx["id"]] = ctx
        elif m == "Runtime.executionContextDestroyed":
            cid = p.get("executionContextId")
            self.contexts.get(sid, {}).pop(cid, None)
        elif m == "Runtime.executionContextsCleared":
            self.contexts[sid] = {}
        elif m in ("Runtime.consoleAPICalled", "Runtime.exceptionThrown", "Log.entryAdded"):
            self.logs.append(self._log_entry(m, p, sid))

    def _world(self, sid, ctx_id):
        ctx = self.contexts.get(sid, {}).get(ctx_id)
        if not ctx:
            return None
        aux = ctx.get("auxData") or {}
        return {"type": aux.get("type", "default" if aux.get("isDefault") else "?"), "name": ctx.get("name", ""),
                "origin": ctx.get("origin", "")}

    def _log_entry(self, m, p, sid):
        s = self.sessions.get(sid, {})
        e = {"t": time.time(), "sid": sid, "target_type": s.get("type"), "target_url": s.get("url")}
        if m == "Runtime.consoleAPICalled":
            parts = []
            for a in p.get("args", []):
                if "value" in a:
                    parts.append(a["value"] if isinstance(a["value"], str) else json.dumps(a["value"], ensure_ascii=False))
                else:
                    parts.append(a.get("description") or a.get("type", ""))
            e.update(kind="console", level=p.get("type"), text=" ".join(str(x) for x in parts),
                     world=self._world(sid, p.get("executionContextId")))
        elif m == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            ex = d.get("exception") or {}
            text = ex.get("description") or ex.get("value") or d.get("text")
            if d.get("text") and d.get("text") not in str(text):
                text = f"{d.get('text')} {text}"
            e.update(kind="exception", level="error", text=str(text), url=d.get("url"), line=d.get("lineNumber"),
                     world=self._world(sid, d.get("executionContextId")))
        else:
            en = p.get("entry", {})
            e.update(kind="log", level=en.get("level"), text=en.get("text"), source=en.get("source"), url=en.get("url"))
        return e

    # ------------------------------------------------------------- helpers
    def wait_for(self, pred, timeout=10.0, interval=0.05, what="condition"):
        end = time.time() + timeout
        while True:
            v = pred()
            if v:
                return v
            if time.time() > end:
                raise CDPError(f"timeout waiting for {what}")
            time.sleep(interval)

    def evaluate(self, sid, expr, await_promise=True, context_id=None, user_gesture=False, timeout=30):
        params = {"expression": expr, "awaitPromise": await_promise, "returnByValue": True,
                  "userGesture": user_gesture}
        if context_id is not None:
            params["contextId"] = context_id
        r = self.send("Runtime.evaluate", params, sid, timeout)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            ex = d.get("exception") or {}
            raise JSError(str(ex.get("description") or ex.get("value") or d.get("text")), d)
        return r.get("result", {}).get("value")

    def close(self):
        self.ws.close()
