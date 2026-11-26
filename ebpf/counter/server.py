#!/usr/bin/env python3
"""Small web UI for the XDP counters (BPF map 'pkt_stats'). Run with sudo (bpftool needs root)."""
import argparse, json, os, shutil, socket, struct, subprocess, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAP_NAME = "pkt_stats"


def find_bpftool():
    for cand in (shutil.which("bpftool"), "/usr/sbin/bpftool", "/usr/local/sbin/bpftool", "/usr/bin/bpftool"):
        if cand and os.access(cand, os.X_OK):
            return cand
    raise RuntimeError("bpftool not found: install it (sudo dnf install bpftool)")


def bpftool(*args):
    out = subprocess.run([find_bpftool(), "-j", *args], capture_output=True, text=True, check=True).stdout
    return json.loads(out) if out.strip() else []


def hexbytes(v):
    return bytes(int(x, 16) for x in v)


def parse_entry(e):
    """One map entry -> (ifindex, packets, bytes), summed over CPUs.
    Handles BTF-formatted output ('formatted') and raw hex output."""
    f = e.get("formatted", e)
    key = f["key"]
    if isinstance(key, list):
        key = struct.unpack("<I", hexbytes(key)[:4])[0]
    packets = nbytes = 0
    for cpu in f.get("values", []):
        v = cpu["value"]
        if isinstance(v, list):
            p, b = struct.unpack("<QQ", hexbytes(v)[:16])
        else:
            p, b = v["packets"], v["bytes"]
        packets += p
        nbytes += b
    return key, packets, nbytes


def collect():
    totals = {}
    # bpfman may create one map per loaded program: sum all maps with our name
    for m in bpftool("map", "show"):
        if m.get("name") != MAP_NAME:
            continue
        for e in bpftool("map", "dump", "id", str(m["id"])):
            idx, p, b = parse_entry(e)
            t = totals.setdefault(idx, [0, 0])
            t[0] += p
            t[1] += b
    rows = []
    for idx, (p, b) in sorted(totals.items()):
        try:
            name = socket.if_indextoname(idx)
        except OSError:
            name = "if%d" % idx
        rows.append({"ifindex": idx, "device": name, "packets": p, "bytes": b})
    return {"time": time.time(), "devices": rows}


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>XDP counters</title>
<style>
body{font-family:Arial,sans-serif;margin:2rem;color:#262626}
h1{font-size:1.4rem}
table{border-collapse:collapse;min-width:40rem}
th,td{padding:.5rem 1rem;text-align:right;border-bottom:1px solid #ddd}
th{background:#da251c;color:#fff}
th:first-child,td:first-child{text-align:left}
#msg{color:#da251c;margin-top:1rem}
</style></head><body>
<h1>XDP receive counters per device</h1>
<table><thead><tr><th>Device</th><th>Packets</th><th>Bytes</th><th>Packets/s</th><th>Mbit/s</th></tr></thead>
<tbody id="rows"></tbody></table><div id="msg"></div>
<script>
let prev = {};
const fmt = n => n.toLocaleString("en-US", {maximumFractionDigits: 1});
async function tick() {
  try {
    const r = await fetch("/stats"); const d = await r.json();
    if (d.error) throw new Error(d.error);
    const tb = document.getElementById("rows"); tb.textContent = "";
    for (const x of d.devices) {
      const p = prev[x.ifindex]; let pps = 0, mbit = 0;
      if (p && d.time > p.time) {
        pps = (x.packets - p.packets) / (d.time - p.time);
        mbit = (x.bytes - p.bytes) * 8 / 1e6 / (d.time - p.time);
      }
      prev[x.ifindex] = {packets: x.packets, bytes: x.bytes, time: d.time};
      const tr = tb.insertRow();
      [x.device, fmt(x.packets), fmt(x.bytes), fmt(pps), fmt(mbit)].forEach(v => tr.insertCell().textContent = v);
    }
    document.getElementById("msg").textContent = d.devices.length ? "" : "No data yet: is the program attached?";
  } catch (e) { document.getElementById("msg").textContent = "Error: " + e.message; }
}
tick(); setInterval(tick, 1000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/stats":
            try:
                body, code = json.dumps(collect()).encode(), 200
            except Exception as e:  # bpftool missing, no permission, ...
                body, code = json.dumps({"error": str(e)}).encode(), 500
            ctype = "application/json"
        elif self.path in ("/", "/index.html"):
            body, code, ctype = PAGE.encode(), 200, "text/html; charset=utf-8"
        else:
            body, code, ctype = b"not found", 404, "text/plain"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    print("Open http://%s:%d" % (a.bind, a.port))
    ThreadingHTTPServer((a.bind, a.port), Handler).serve_forever()
