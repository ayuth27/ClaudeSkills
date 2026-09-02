"""Minimal Chrome DevTools Protocol client. Standard library only.

Talks to any Chromium-family browser (Brave, Chrome, Edge) started with
--remote-debugging-port. Reuses the browser's existing profile, so any site
you are already logged into stays logged in.
"""

import base64
import json
import os
import socket
import struct
import time
import urllib.parse
import urllib.request

DEFAULT_PORT = 9222


class CDPError(RuntimeError):
    pass


class _WebSocket:
    """Just enough RFC6455 to carry CDP JSON messages."""

    def __init__(self, url, connect_timeout=15):
        u = urllib.parse.urlparse(url)
        host = u.hostname or "127.0.0.1"
        port = u.port or 80
        path = u.path + (("?" + u.query) if u.query else "")
        self.sock = socket.create_connection((host, port), timeout=connect_timeout)
        self.sock.settimeout(connect_timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key)
        )
        self.sock.sendall(req.encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("browser closed the connection during handshake")
            raw += chunk
        head, _, rest = raw.partition(b"\r\n\r\n")
        status = head.split(b"\r\n")[0].decode(errors="replace")
        if "101" not in status:
            raise CDPError("websocket handshake failed: %s" % status)
        self._buf = bytearray(rest)
        self._frag = bytearray()
        self.sock.settimeout(0.4)

    def send(self, text):
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        n = len(payload)
        mask = os.urandom(4)
        if n < 126:
            header.append(0x80 | n)
        elif n < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _pull(self):
        """Read whatever is available. Returns False on timeout, raises on close."""
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            return False
        if not chunk:
            raise CDPError("browser closed the websocket")
        self._buf += chunk
        return True

    def _take_frame(self):
        b = self._buf
        if len(b) < 2:
            return None
        fin = b[0] & 0x80
        opcode = b[0] & 0x0F
        masked = b[1] & 0x80
        ln = b[1] & 0x7F
        off = 2
        if ln == 126:
            if len(b) < off + 2:
                return None
            ln = struct.unpack_from(">H", b, off)[0]
            off += 2
        elif ln == 127:
            if len(b) < off + 8:
                return None
            ln = struct.unpack_from(">Q", b, off)[0]
            off += 8
        mask = None
        if masked:
            if len(b) < off + 4:
                return None
            mask = bytes(b[off:off + 4])
            off += 4
        if len(b) < off + ln:
            return None
        payload = bytes(b[off:off + ln])
        if mask:
            payload = bytes(p ^ mask[i % 4] for i, p in enumerate(payload))
        del b[:off + ln]
        return fin, opcode, payload

    def recv(self, deadline):
        """Return the next complete text message, or None once deadline passes."""
        while True:
            frame = self._take_frame()
            if frame is None:
                if time.time() > deadline:
                    return None
                self._pull()
                continue
            fin, opcode, payload = frame
            if opcode == 0x8:  # close
                raise CDPError("browser closed the websocket")
            if opcode == 0x9:  # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if opcode == 0xA:  # pong
                continue
            if opcode == 0x0:
                self._frag += payload
            else:
                self._frag = bytearray(payload)
            if fin:
                out = bytes(self._frag)
                self._frag = bytearray()
                return out.decode("utf-8", errors="replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def http_json(port, path, method="GET"):
    url = "http://127.0.0.1:%d%s" % (port, path)
    # Never route browser-debug traffic through an outbound proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, method=method)
    with opener.open(req, timeout=10) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body) if body.strip().startswith(("{", "[")) else body


def new_tab(port, url):
    """Open a tab. Chrome 111+ requires PUT on /json/new; older builds want GET."""
    path = "/json/new?" + urllib.parse.quote(url, safe=":/?=&%#")
    try:
        return http_json(port, path, method="PUT")
    except Exception:
        return http_json(port, path, method="GET")


class Browser:
    """A CDP session bound to one page (tab)."""

    def __init__(self, port=DEFAULT_PORT):
        self.port = port
        self.ws = None
        self._id = 0
        self.events = []

    # -- connection -------------------------------------------------------
    def list_pages(self):
        try:
            targets = http_json(self.port, "/json/list")
        except Exception as exc:  # noqa: BLE001 - surfaced as guidance
            raise CDPError(
                "cannot reach the browser on port %d (%s). Start Brave with "
                "--remote-debugging-port=%d; see references/setup-brave.md"
                % (self.port, exc, self.port)
            )
        return [t for t in targets if t.get("type") == "page"]

    def attach(self, url_contains, open_url=None, open_if_missing=True):
        """Attach to the first tab whose URL contains url_contains."""
        def find():
            return next(
                (p for p in self.list_pages() if url_contains in (p.get("url") or "")), None
            )

        match = find()
        if match is None:
            if not open_if_missing:
                raise CDPError("no open tab matching %r" % url_contains)
            new_tab(self.port, open_url or url_contains)
            for _ in range(40):
                time.sleep(0.5)
                match = find()
                if match:
                    break
            if match is None:
                raise CDPError("could not open a tab for %r" % (open_url or url_contains))
        self.ws = _WebSocket(match["webSocketDebuggerUrl"])
        self.page_url = match.get("url", "")
        self.call("Runtime.enable")
        self.call("Page.enable")
        self.call("Network.enable")
        return self

    def close(self):
        if self.ws:
            self.ws.close()
            self.ws = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- protocol ---------------------------------------------------------
    def call(self, method, params=None, timeout=60):
        if self.ws is None:
            raise CDPError("not attached to a tab")
        self._id += 1
        msg_id = self._id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while True:
            raw = self.ws.recv(deadline)
            if raw is None:
                raise CDPError("timed out waiting for %s" % method)
            msg = json.loads(raw)
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise CDPError("%s failed: %s" % (method, msg["error"].get("message")))
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
                if len(self.events) > 4000:
                    del self.events[:2000]

    def pump(self, seconds):
        """Collect events for a while without issuing a command."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            raw = self.ws.recv(deadline)
            if raw is None:
                return
            msg = json.loads(raw)
            if "method" in msg:
                self.events.append(msg)

    def drain_events(self, method):
        return [e for e in self.events if e.get("method") == method]

    def clear_events(self):
        self.events = []

    # -- page helpers -----------------------------------------------------
    def js(self, expression, timeout=60):
        """Evaluate JS in the page and return the value (awaits promises)."""
        res = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if res.get("exceptionDetails"):
            det = res["exceptionDetails"]
            text = (det.get("exception") or {}).get("description") or det.get("text")
            raise CDPError("page script error: %s" % text)
        return res.get("result", {}).get("value")

    def key(self, key_name, code, key_code, modifiers=0):
        for kind in ("keyDown", "keyUp"):
            self.call(
                "Input.dispatchKeyEvent",
                {
                    "type": kind,
                    "key": key_name,
                    "code": code,
                    "windowsVirtualKeyCode": key_code,
                    "nativeVirtualKeyCode": key_code,
                    "modifiers": modifiers,
                },
            )

    def press_enter(self):
        self.key("Enter", "Enter", 13)

    def bring_to_front(self):
        try:
            self.call("Page.bringToFront")
        except CDPError:
            pass
