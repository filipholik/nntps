#!/usr/bin/env python3
"""Web UI for filter.c: traffic per MAC address + block / allow buttons.
Run with sudo (bpftool needs root)."""
import argparse, json, os, re, shutil, struct, subprocess, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATS, BLOCKED = "mac_stats", "blocked"
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def find_bpftool():
    for cand in (shutil.which("bpftool"), "/usr/sbin/bpftool", "/usr/local/sbin/bpftool", "/usr/bin/bpftool"):
        if cand and os.access(cand, os.X_OK):
            return cand
    raise RuntimeError("bpftool not found: install it (sudo dnf install bpftool)")


def bpftool(*args, js=True):
    cmd = [find_bpftool()] + (["-j"] if js else []) + list(args)
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return json.loads(out) if js and out.strip() else []


def map_ids(name):
    # bpfman may create one map per loaded program (one per interface): handle all of them
    return [m["id"] for m in bpftool("map", "show") if m.get("name") == name]


def hexbytes(v):
    return bytes(int(x, 16) for x in v)


def key_of(k):
    return struct.unpack("<Q", hexbytes(k)[:8])[0] if isinstance(k, list) else k


def mac_str(n):
    return ":".join("%02x" % ((n >> s) & 0xFF) for s in range(40, -8, -8))


def parse_stats(e):
    f = e.get("formatted", e)
    pk = by = 0
    for cpu in f.get("values", []):
        v = cpu["value"]
        p, b = struct.unpack("<QQ", hexbytes(v)[:16]) if isinstance(v, list) else (v["packets"], v["bytes"])
        pk += p
        by += b
    return key_of(f["key"]), pk, by


def parse_blocked(e):
    f = e.get("formatted", e)
    v = f["value"]
    d = struct.unpack("<Q", hexbytes(v)[:8])[0] if isinstance(v, list) else v["dropped"]
    return key_of(f["key"]), d


def collect():
    rows = {}
    for i in map_ids(STATS):
        for e in bpftool("map", "dump", "id", str(i)):
            k, p, b = parse_stats(e)
            r = rows.setdefault(k, {"packets": 0, "bytes": 0, "dropped": 0, "blocked": False})
            r["packets"] += p
            r["bytes"] += b
    for i in map_ids(BLOCKED):
        for e in bpftool("map", "dump", "id", str(i)):
            k, d = parse_blocked(e)
            r = rows.setdefault(k, {"packets": 0, "bytes": 0, "dropped": 0, "blocked": False})
            r["blocked"] = True
            r["dropped"] += d
    out = [dict(mac=mac_str(k), **r) for k, r in rows.items()]
    out.sort(key=lambda r: (-r["packets"], r["mac"]))
    return {"time": time.time(), "devices": out}


def key_hex(n):
    return ["key", "hex"] + ["%02x" % b for b in n.to_bytes(8, "little")]


def key_args(mac):
    return key_hex(int(mac.replace(":", ""), 16))


def set_blocked(mac, block):
    if not MAC_RE.match(mac):
        raise ValueError("invalid MAC address")
    if int(mac[:2], 16) & 1:
        raise ValueError("multicast/broadcast addresses cannot be blocked")
    ids = map_ids(BLOCKED)
    if not ids:
        raise RuntimeError("map 'blocked' not found: is macfilter loaded?")
    for i in ids:
        try:
            if block:
                bpftool("map", "update", "id", str(i), *key_args(mac), "value", "hex", *["00"] * 8, "noexist", js=False)
            else:
                bpftool("map", "delete", "id", str(i), *key_args(mac), js=False)
        except subprocess.CalledProcessError:
            pass  # already blocked / already allowed


def clear_counters():
    """Forget all learned MACs (they reappear with fresh counters) and zero the drop counters."""
    for i in map_ids(STATS):
        for e in bpftool("map", "dump", "id", str(i)):
            try:
                bpftool("map", "delete", "id", str(i), *key_hex(parse_stats(e)[0]), js=False)
            except subprocess.CalledProcessError:
                pass
    for i in map_ids(BLOCKED):
        for e in bpftool("map", "dump", "id", str(i)):
            try:
                bpftool("map", "update", "id", str(i), *key_hex(parse_blocked(e)[0]), "value", "hex", *["00"] * 8, "exist", js=False)
            except subprocess.CalledProcessError:
                pass


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>MAC filter</title>
<style>
body{font-family:Arial,sans-serif;margin:2rem;color:#262626}
h1{font-size:1.4rem}
table{border-collapse:collapse;min-width:48rem}
th,td{padding:.5rem 1rem;text-align:right;border-bottom:1px solid #ddd}
th{background:#da251c;color:#fff}
th:first-child,td:first-child,td.st{text-align:left}
tr.blk td{background:#fdecea}
button{padding:.3rem .9rem;cursor:pointer}
#msg{color:#da251c;margin-top:1rem}
input{padding:.3rem;width:12rem;font-family:monospace}
</style></head><body>
<h1>Traffic per MAC address</h1>
<table><thead><tr><th>MAC</th><th>Packets</th><th>Bytes</th><th>Packets/s</th><th>Mbit/s</th><th>Dropped</th><th>Status</th><th></th></tr></thead>
<tbody id="rows"></tbody></table>
<p><input id="mac" placeholder="aa:bb:cc:dd:ee:ff"> <button onclick="act('block', document.getElementById('mac').value.trim().toLowerCase())">Block MAC</button>
 <button onclick="prev = {}; act('clear')">Clear counters</button></p>
<div id="msg"></div>
<script>
let prev = {};
const fmt = n => n.toLocaleString("en-US", {maximumFractionDigits: 1});
async function act(what, mac) {
  try {
    const r = await fetch("/" + what, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mac})});
    const d = await r.json(); if (d.error) throw new Error(d.error);
    tick();
  } catch (e) { document.getElementById("msg").textContent = "Error: " + e.message; }
}
async function tick() {
  try {
    const r = await fetch("/stats"); const d = await r.json();
    if (d.error) throw new Error(d.error);
    const tb = document.getElementById("rows"); tb.textContent = "";
    for (const x of d.devices) {
      const p = prev[x.mac]; let pps = 0, mbit = 0;
      if (p && d.time > p.time) {
        pps = (x.packets - p.packets) / (d.time - p.time);
        mbit = (x.bytes - p.bytes) * 8 / 1e6 / (d.time - p.time);
      }
      prev[x.mac] = {packets: x.packets, bytes: x.bytes, time: d.time};
      const tr = tb.insertRow(); if (x.blocked) tr.className = "blk";
      [x.mac, fmt(x.packets), fmt(x.bytes), fmt(pps), fmt(mbit), fmt(x.dropped), x.blocked ? "BLOCKED" : "allowed"]
        .forEach((v, i) => { const c = tr.insertCell(); c.textContent = v; if (i == 6) c.className = "st"; });
      const b = document.createElement("button"); b.textContent = x.blocked ? "Allow" : "Block";
      b.onclick = () => act(x.blocked ? "unblock" : "block", x.mac); tr.insertCell().appendChild(b);
    }
    document.getElementById("msg").textContent = d.devices.length ? "" : "No MAC addresses seen yet: is the program attached?";
  } catch (e) { document.getElementById("msg").textContent = "Error: " + e.message; }
}
tick(); setInterval(tick, 1000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype="application/json"):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser closed the connection (reload, closed tab): nothing to do

    def do_GET(self):
        if self.path == "/stats":
            try:
                code, body = 200, json.dumps(collect()).encode()
            except Exception as e:
                code, body = 500, json.dumps({"error": str(e)}).encode()
            self.reply(code, body)
        elif self.path in ("/", "/index.html"):
            self.reply(200, PAGE.encode(), "text/html; charset=utf-8")
        else:
            self.reply(404, b"not found", "text/plain")

    def do_POST(self):
        try:
            # JSON content type: a web page on another site cannot send this cross-origin
            if not self.headers.get("Content-Type", "").startswith("application/json") or self.path not in ("/block", "/unblock", "/clear"):
                raise ValueError("bad request")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if self.path == "/clear":
                clear_counters()
            else:
                set_blocked(body["mac"].lower(), self.path == "/block")
            self.reply(200, b'{"ok": true}')
        except Exception as e:
            self.reply(400, json.dumps({"error": str(e)}).encode())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    print("Open http://%s:%d" % (a.bind, a.port))
    ThreadingHTTPServer((a.bind, a.port), Handler).serve_forever()
