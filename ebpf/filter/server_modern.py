#!/usr/bin/env python3
"""Web UI for filter.c: traffic per MAC address + block / allow buttons.
Run with sudo (bpftool needs root)."""
import argparse, collections, json, os, re, shutil, struct, subprocess, threading, time
from urllib.parse import parse_qs, urlparse
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


class Sampler(threading.Thread):
    """Samples the maps every `interval` seconds, keeps 15 minutes of history and the rates.
    Sample = {"t": time, "d": {mac: [packets, bytes, dropped, blocked, pkt/s, Mbit/s, drops/s]}}"""

    def __init__(self, interval):
        super().__init__(daemon=True)
        self.interval = interval
        self.hist = collections.deque(maxlen=int(900 / interval) + 5)
        self.prev, self.gen, self.error = {}, 0, None
        self.lock, self.wake = threading.Lock(), threading.Event()

    def kick(self):  # sample again right now (after block / unblock)
        self.wake.set()

    def reset(self):  # after "Clear counters": forget the history
        with self.lock:
            self.hist.clear()
            self.prev.clear()
            self.gen += 1
        self.kick()

    def sample(self):
        g = self.gen
        data = collect()
        t = data["time"]
        with self.lock:
            if g != self.gen:  # cleared while we were reading: discard
                return
            d = {}
            for x in data["devices"]:
                p = self.prev.get(x["mac"])
                pps = mb = dr = 0.0
                if p and t > p[0]:
                    dt = t - p[0]
                    pps = max(0.0, (x["packets"] - p[1]) / dt)
                    mb = max(0.0, (x["bytes"] - p[2]) * 8 / 1e6 / dt)
                    dr = max(0.0, (x["dropped"] - p[3]) / dt)
                self.prev[x["mac"]] = (t, x["packets"], x["bytes"], x["dropped"])
                d[x["mac"]] = [x["packets"], x["bytes"], x["dropped"], int(x["blocked"]), round(pps, 2), round(mb, 4), round(dr, 2)]
            self.hist.append({"t": round(t, 3), "d": d})
            self.error = None

    def run(self):
        while True:
            try:
                self.sample()
            except Exception as e:  # bpftool missing, no permission, ...
                self.error = str(e)
            self.wake.wait(self.interval)
            self.wake.clear()

    def history(self, since, gen):
        with self.lock:
            if self.error:
                return {"error": self.error}
            full = gen != self.gen or since < 0
            s = list(self.hist) if full else [x for x in self.hist if x["t"] > since]
            return {"gen": self.gen, "now": time.time(), "interval": self.interval, "full": full, "samples": s}


SAMPLER = None


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MAC filter</title>
<style>
:root{--bg:#f4f6fa;--surface:#fff;--text:#1b2433;--muted:#6b7686;--line:#e4e8ef;--ok:#16a34a;--ok-bg:#e8f7ee;--ok-line:#b7e4c7;--bad:#dc2626;--bad-bg:#fdecec;--bad-line:#f5b5b5;--accent:#2563eb;--on:#fff;--shadow:0 1px 2px rgba(16,24,40,.06),0 4px 14px rgba(16,24,40,.06)}
@media (prefers-color-scheme:dark){:root{--bg:#0f1520;--surface:#182130;--text:#e8edf5;--muted:#93a0b4;--line:#263247;--ok:#4ade80;--ok-bg:#12301f;--ok-line:#1f5a39;--bad:#f87171;--bad-bg:#3a1618;--bad-line:#7a2a2e;--accent:#60a5fa;--on:#0b1220;--shadow:0 1px 2px rgba(0,0,0,.4)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:1.5rem}
header{display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap;margin-bottom:1.25rem}
h1{display:flex;align-items:center;gap:.6rem;font-size:1.4rem;margin:0}
h1 svg{width:28px;height:28px;color:var(--accent)}
.live{display:flex;align-items:center;gap:.5rem;color:var(--muted);font-size:.85rem}
.dot{width:10px;height:10px;border-radius:50%;background:var(--ok);animation:pulse 2s infinite}
.live.off .dot{background:var(--bad);animation:none}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(22,163,74,.5)}70%{box-shadow:0 0 0 8px rgba(22,163,74,0)}100%{box-shadow:0 0 0 0 rgba(22,163,74,0)}}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.9rem;margin-bottom:1.25rem}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:.9rem 1rem;box-shadow:var(--shadow)}
.tile .k{color:var(--muted);font-size:.8rem}
.tile .v{font-size:1.5rem;font-weight:700;margin-top:.15rem;font-variant-numeric:tabular-nums}
.tile.ok .v{color:var(--ok)}.tile.bad .v{color:var(--bad)}
.bar{display:flex;flex-wrap:wrap;gap:.7rem;align-items:center;margin-bottom:1.1rem}
.seg{display:flex;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:3px}
.seg button{border:0;background:none;color:var(--muted);padding:.4rem .9rem;border-radius:8px;cursor:pointer;font:inherit}
.seg button.on{background:var(--accent);color:var(--on)}
.spacer{flex:1}
.field{display:flex;align-items:center;gap:.4rem;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:.2rem .25rem .2rem .7rem}
.field input{border:0;outline:0;background:none;color:var(--text);font:13px ui-monospace,Menlo,Consolas,monospace;width:11rem}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:.45rem;border:1px solid transparent;border-radius:9px;padding:.5rem .9rem;font:inherit;font-weight:600;cursor:pointer;transition:filter .15s,transform .05s}
.btn:hover{filter:brightness(.93)}.btn:active{transform:scale(.98)}
.btn svg{width:16px;height:16px}
.btn.bad{background:var(--bad);color:var(--on)}.btn.ok{background:var(--ok);color:var(--on)}
.btn.ghost{background:var(--surface);border-color:var(--line);color:var(--text)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:1rem}
.card{background:var(--surface);border:1px solid var(--ok-line);border-radius:16px;padding:1rem;box-shadow:var(--shadow);transition:background .2s,border-color .2s}
.card.bad{background:var(--bad-bg);border-color:var(--bad-line)}
.top{display:flex;align-items:center;gap:.8rem;margin-bottom:.9rem}
.ico{width:44px;height:44px;border-radius:12px;display:grid;place-items:center}.ico svg{width:24px;height:24px}
.card.ok .ico{background:var(--ok-bg);color:var(--ok)}.card.bad .ico{background:var(--bad);color:var(--on)}
.mac{font:600 15px ui-monospace,Menlo,Consolas,monospace}
.pill{display:inline-flex;align-items:center;gap:.3rem;font-size:.75rem;font-weight:700;padding:.15rem .55rem;border-radius:99px;margin-top:.25rem}
.pill svg{width:13px;height:13px}
.card.ok .pill{background:var(--ok-bg);color:var(--ok)}.card.bad .pill{background:var(--bad);color:var(--on)}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:.6rem;margin-bottom:.9rem}
.stats .k{display:block;color:var(--muted);font-size:.72rem}
.stats .v{font-weight:600;font-variant-numeric:tabular-nums}
.stats .drop.on .v{color:var(--bad)}
.card .btn{width:100%}
.empty{text-align:center;color:var(--muted);padding:2.5rem}
.toast{position:fixed;bottom:1.2rem;left:50%;transform:translateX(-50%);background:var(--bad);color:var(--on);padding:.6rem 1rem;border-radius:10px;box-shadow:var(--shadow);display:none}
.tabs{display:flex;gap:.4rem;margin-bottom:1.1rem;border-bottom:1px solid var(--line)}
.tabs button{display:inline-flex;align-items:center;gap:.5rem;border:0;background:none;color:var(--muted);padding:.6rem 1rem;font:inherit;font-weight:600;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.tabs button svg{width:18px;height:18px}
.tabs button.on{color:var(--accent);border-bottom-color:var(--accent)}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:1rem;box-shadow:var(--shadow);margin-bottom:1rem}
.panel h2{font-size:1rem;margin:0 0 .6rem}
canvas{width:100%;height:280px;display:block}
.legend{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.8rem}
.chip{display:inline-flex;align-items:center;gap:.4rem;border:1px solid var(--line);background:var(--surface);color:var(--text);padding:.25rem .65rem;border-radius:99px;cursor:pointer;font:12px ui-monospace,Menlo,Consolas,monospace}
.chip i{width:10px;height:10px;border-radius:50%}
.chip.off{opacity:.4}.chip.blk{border-color:var(--bad-line);color:var(--bad)}
.note{color:var(--muted);font-size:.8rem;margin:.2rem 0 0}
</style></head><body><div class="wrap">
<header><h1><span data-i="check"></span>MAC filter</h1><div class="live" id="live"><span class="dot"></span><span id="livetxt">Live</span></div></header>
<div class="tiles">
<div class="tile"><div class="k">Devices</div><div class="v" id="t-dev">0</div></div>
<div class="tile ok"><div class="k">Allowed</div><div class="v" id="t-ok">0</div></div>
<div class="tile bad"><div class="k">Blocked</div><div class="v" id="t-bad">0</div></div>
<div class="tile"><div class="k">Traffic (Mbit/s)</div><div class="v" id="t-rate">0</div></div>
<div class="tile"><div class="k">Dropped frames</div><div class="v" id="t-drop">0</div></div>
</div>
<nav class="tabs" id="tabs"><button data-tab="devices" class="on"><span data-i="grid"></span>Devices</button><button data-tab="graphs"><span data-i="chart"></span>Graphs</button></nav>
<div id="tab-devices">
<div class="bar">
<div class="seg" id="seg"><button data-f="all" class="on">All</button><button data-f="ok">Allowed</button><button data-f="bad">Blocked</button></div>
<div class="spacer"></div>
<div class="field"><input id="mac" placeholder="aa:bb:cc:dd:ee:ff"><button class="btn bad" id="addblock"><span data-i="ban"></span>Block MAC</button></div>
<button class="btn ghost" id="clear"><span data-i="trash"></span>Clear counters</button>
</div>
<div class="grid" id="grid"></div><div class="empty" id="empty" style="display:none"></div>
</div>
<div id="tab-graphs" style="display:none">
<div class="bar">
<div class="seg" id="mseg"><button data-f="mb" class="on">Mbit/s</button><button data-f="pps">Packets/s</button><button data-f="dr">Dropped/s</button></div>
<div class="spacer"></div>
<div class="seg" id="wseg"><button data-f="60" class="on">1 min</button><button data-f="300">5 min</button><button data-f="900">15 min</button></div>
</div>
<div class="panel"><h2 id="h-total"></h2><canvas id="c-total" style="height:170px"></canvas></div>
<div class="panel"><h2 id="h-dev"></h2><canvas id="c-dev"></canvas><div class="legend" id="legend"></div>
<p class="note">Click a device to hide or show it. Dashed lines are blocked devices. History is kept on the server (up to 15 minutes) and cleared with Clear counters.</p></div>
</div>
</div><div class="toast" id="toast"></div>
<script>
const P = {
  device: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  check: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/>'
};
const ic = n => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + P[n] + '</svg>';
document.querySelectorAll('[data-i]').forEach(e => e.innerHTML = ic(e.dataset.i));
const $ = id => document.getElementById(id);
const num = n => n.toLocaleString('en-US', {maximumFractionDigits: 1});
const bytes = n => { const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return (i ? n.toFixed(1) : n) + ' ' + u[i]; };
const PAL = ['#2563eb', '#f59e0b', '#10b981', '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1', '#14b8a6'];
const METRICS = {mb: ['Mbit/s', 'Mbit/s', 5], pps: ['Packets/s', 'pkt/s', 4], dr: ['Dropped/s', 'drop/s', 6]};
/* server sample: {t, d: {mac: [packets, bytes, dropped, blocked, pkt/s, Mbit/s, drops/s]}} */
let samples = [], gen = null, off = 0, haveOff = false, interval = 1, polling = false;
let cards = {}, filter = 'all', colors = {}, hidden = new Set(), metric = 'mb', win = 60, tab = 'devices', legendKey = '', titleKey = '';
let tC = 0, tG = 0;
const colorOf = m => colors[m] || (colors[m] = PAL[Object.keys(colors).length % PAL.length]);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function toast(msg) { const t = $('toast'); t.textContent = msg; t.style.display = 'block'; clearTimeout(t.h); t.h = setTimeout(() => t.style.display = 'none', 4000); }
function setLive(ok, msg) { $('live').className = 'live' + (ok ? '' : ' off'); $('livetxt').textContent = ok ? 'Live' : (msg || 'No connection'); }

async function act(what, mac) {
  try {
    const r = await fetch('/' + what, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mac})});
    const d = await r.json(); if (d.error) throw new Error(d.error);
    setTimeout(poll, 400); return true;
  } catch (e) { toast('Error: ' + e.message); return false; }
}

/* ---- data from the server (history lives on the server) ---- */
async function poll() {
  if (polling) return; polling = true;
  try {
    const q = (gen === null || !samples.length) ? '' : '?since=' + samples[samples.length - 1].t + '&gen=' + gen;
    const r = await fetch('/history' + q); const d = await r.json();
    if (d.error) throw new Error(d.error);
    if (d.full) samples = [];
    gen = d.gen; interval = d.interval;
    for (const s of d.samples) samples.push(s);
    if (samples.length) { const keep = samples[samples.length - 1].t - 905; let k = 0; while (k < samples.length && samples[k].t < keep) k++; if (k) samples.splice(0, k); }
    const o = d.now - Date.now() / 1000; off = haveOff ? off * 0.9 + o * 0.1 : o; haveOff = true;
    setLive(true);
  } catch (e) { setLive(false, 'Error: ' + e.message); }
  polling = false;
}

/* ---- smooth display: show the state a little in the past and interpolate between samples ---- */
function dispT() {
  const latest = samples[samples.length - 1].t;
  return Math.min(Date.now() / 1000 + off - (interval + 0.8), latest);
}
function locate(D) {
  let i = samples.length - 1; while (i > 0 && samples[i].t > D) i--;
  const a = samples[i], b = samples[i + 1];
  const f = b && b.t > a.t ? Math.max(0, Math.min(1, (D - a.t) / (b.t - a.t))) : 0;
  return [a, b, f];
}
function lerp(a, b, f) {
  if (!a && !b) return null; if (!a) return b; if (!b) return a;
  return a.map((v, k) => k == 3 ? b[k] : v + (b[k] - v) * f);
}
function view(D) {
  const last = samples[samples.length - 1], [a, b, f] = locate(D), out = [];
  for (const mac in last.d) {
    const v = lerp(a.d[mac], b && b.d[mac], f) || last.d[mac];
    out.push({mac, packets: v[0], bytes: v[1], dropped: v[2], blocked: !!last.d[mac][3], pps: v[4], mb: v[5]});
  }
  return out.sort((x, y) => x.mac < y.mac ? -1 : 1);
}

/* ---- devices tab ---- */
function mkCard(mac) {
  const c = document.createElement('div'); c.className = 'card ok';
  c.innerHTML = '<div class="top"><div class="ico">' + ic('device') + '</div><div><div class="mac"></div><span class="pill"></span></div></div>'
    + '<div class="stats">' + ['Packets', 'Bytes', 'Packets/s', 'Mbit/s', 'Dropped'].map((k, i) => '<div class="' + (k == 'Dropped' ? 'drop' : '') + '"><span class="k">' + k + '</span><span class="v" data-s="' + i + '">0</span></div>').join('') + '</div>'
    + '<button class="btn"></button>';
  c.querySelector('.mac').textContent = mac;
  c.querySelector('.btn').onclick = () => act(c.dataset.blocked == '1' ? 'unblock' : 'block', mac);
  return c;
}
function update(c, x) {
  const b = x.blocked; c.dataset.blocked = b ? '1' : '0'; c.className = 'card ' + (b ? 'bad' : 'ok');
  c.querySelector('.pill').innerHTML = ic(b ? 'ban' : 'check') + (b ? 'Blocked' : 'Allowed');
  const btn = c.querySelector('.btn'); btn.className = 'btn ' + (b ? 'ok' : 'bad');
  btn.innerHTML = ic(b ? 'check' : 'ban') + (b ? 'Allow device' : 'Block device');
  const v = [num(Math.round(x.packets)), bytes(Math.round(x.bytes)), num(x.pps), num(x.mb), num(Math.round(x.dropped))];
  c.querySelectorAll('[data-s]').forEach(e => e.textContent = v[e.dataset.s]);
  c.querySelector('.drop').className = 'drop' + (x.dropped >= 0.5 ? ' on' : '');
}
function tiles(list) {
  let ok = 0, bad = 0, mbit = 0, drop = 0;
  for (const x of list) { x.blocked ? bad++ : ok++; mbit += x.mb; drop += x.dropped; }
  $('t-dev').textContent = list.length; $('t-ok').textContent = ok; $('t-bad').textContent = bad;
  $('t-rate').textContent = num(mbit); $('t-drop').textContent = num(Math.round(drop));
}
function renderCards(list) {
  const shown = list.filter(x => filter == 'all' || (filter == 'bad') == x.blocked);
  const grid = $('grid'); let i = 0;
  for (const x of shown) {
    const c = cards[x.mac] || (cards[x.mac] = mkCard(x.mac)); update(c, x);
    if (grid.children[i] !== c) grid.insertBefore(c, grid.children[i] || null); i++;
  }
  while (grid.children.length > i) grid.lastChild.remove();
  $('empty').style.display = shown.length ? 'none' : 'block';
  $('empty').textContent = list.length ? 'No devices in this view.' : 'No MAC addresses seen yet. Is the program attached and is there traffic?';
}

/* ---- graphs tab ---- */
function niceMax(m) { if (!(m > 0)) return 1; const e = Math.pow(10, Math.floor(Math.log10(m))), f = m / e; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e; }
function fmtV(v) { return v == 0 ? '0' : v >= 1000 ? (v / 1000).toFixed(v >= 10000 ? 0 : 1) + 'k' : v >= 100 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v.toFixed(2); }
function fmtAgo(t) { const s = Math.round(-t); if (s <= 0) return 'now'; return s >= 60 ? Math.floor(s / 60) + 'm' + (s % 60 ? (s % 60) + 's' : '') : s + 's'; }

function drawChart(cv, series, unit) {
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight; if (!W) return;
  cv.width = W * dpr; cv.height = H * dpr; const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  const L = 48, R = 12, T = 10, B = 26, w = W - L - R, h = H - T - B;
  let m = 0; for (const s of series) for (const p of s.pts) if (p[1] != null && p[1] > m) m = p[1];
  const ymax = niceMax(m), X = t => L + (t + win) / win * w, Y = v => T + h - (v / ymax) * h;
  g.font = '11px system-ui,sans-serif'; g.fillStyle = css('--muted'); g.strokeStyle = css('--line'); g.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = T + h * i / 4; g.beginPath(); g.moveTo(L, y); g.lineTo(W - R, y); g.stroke();
    g.textAlign = 'right'; g.textBaseline = 'middle'; g.fillText(fmtV(ymax * (4 - i) / 4), L - 6, y);
  }
  g.textAlign = 'center'; g.textBaseline = 'top';
  for (let i = 0; i <= 4; i++) g.fillText(fmtAgo(-win * (4 - i) / 4), L + w * i / 4, T + h + 7);
  g.save(); g.beginPath(); g.rect(L, T - 2, w, h + 4); g.clip(); g.lineJoin = 'round';
  for (const s of series) {
    g.strokeStyle = s.color; g.lineWidth = 2; g.setLineDash(s.dash || []); g.beginPath(); let open = false;
    for (const [t, v] of s.pts) { if (v == null) { open = false; continue; } if (open) g.lineTo(X(t), Y(v)); else { g.moveTo(X(t), Y(v)); open = true; } }
    g.stroke(); g.setLineDash([]);
    if (s.fill && s.pts.length > 1) {
      g.beginPath(); s.pts.forEach(([t, v], i) => i ? g.lineTo(X(t), Y(v || 0)) : g.moveTo(X(t), Y(v || 0)));
      g.lineTo(X(s.pts[s.pts.length - 1][0]), T + h); g.lineTo(X(s.pts[0][0]), T + h); g.closePath();
      g.globalAlpha = 0.15; g.fillStyle = s.color; g.fill(); g.globalAlpha = 1;
    }
  }
  g.restore();
  if (cv._hx != null && series.length && series[0].pts.length) {
    const t = (cv._hx - L) / w * win - win; let bi = 0, bd = 1e9;
    series[0].pts.forEach((p, i) => { const dd = Math.abs(p[0] - t); if (dd < bd) { bd = dd; bi = i; } });
    const tx = X(series[0].pts[bi][0]);
    g.strokeStyle = css('--muted'); g.lineWidth = 1; g.setLineDash([4, 3]); g.beginPath(); g.moveTo(tx, T); g.lineTo(tx, T + h); g.stroke(); g.setLineDash([]);
    const items = series.map(s => [s, s.pts[bi][1]]).filter(a => a[1] != null).slice(0, 10);
    for (const [s, v] of items) { g.fillStyle = s.color; g.beginPath(); g.arc(tx, Y(v), 3.5, 0, 6.2832); g.fill(); }
    const lines = [fmtAgo(series[0].pts[bi][0])].concat(items.map(([s, v]) => s.name + '  ' + fmtV(v) + ' ' + unit));
    g.font = '12px system-ui,sans-serif'; const bw = Math.max(...lines.map(l => g.measureText(l).width)) + 34, bh = lines.length * 18 + 8;
    let bx = tx + 12; if (bx + bw > W - 4) bx = tx - 12 - bw; const by = T + 4;
    g.fillStyle = css('--surface'); g.strokeStyle = css('--line'); g.beginPath(); g.rect(bx, by, bw, bh); g.fill(); g.stroke();
    g.textAlign = 'left'; g.textBaseline = 'middle';
    lines.forEach((l, i) => {
      const y = by + 4 + i * 18 + 9;
      if (i) { g.fillStyle = items[i - 1][0].color; g.fillRect(bx + 8, y - 4, 8, 8); }
      g.fillStyle = css(i ? '--text' : '--muted'); g.fillText(l, bx + (i ? 22 : 8), y);
    });
  }
}

function drawGraphs(D) {
  const [title, unit, mi] = METRICS[metric], last = samples[samples.length - 1];
  const lo = D - win; let i0 = 0; while (i0 < samples.length && samples[i0].t < lo) i0++; if (i0 > 0) i0--;
  const rows = []; for (let k = i0; k < samples.length && samples[k].t <= D; k++) rows.push(samples[k]);
  if (!rows.length || rows[rows.length - 1].t < D) {   /* head of the line: interpolated state at D */
    const [a, b, f] = locate(D), head = {t: D, d: {}};
    for (const mac in last.d) head.d[mac] = lerp(a.d[mac], b && b.d[mac], f) || last.d[mac];
    rows.push(head);
  }
  const set = new Set(), blk = {};
  rows.forEach(r => { for (const m in r.d) { set.add(m); blk[m] = !!r.d[m][3]; } });
  const macs = Array.from(set).sort();
  const key = macs.map(m => m + (blk[m] ? '!' : '') + (hidden.has(m) ? 'x' : '')).join();
  if (key !== legendKey) {
    legendKey = key; const lg = $('legend'); lg.textContent = '';
    for (const m of macs) {
      const b = document.createElement('button'); b.className = 'chip' + (hidden.has(m) ? ' off' : '') + (blk[m] ? ' blk' : '');
      const i = document.createElement('i'); i.style.background = colorOf(m); b.appendChild(i);
      b.appendChild(document.createTextNode(m + (blk[m] ? ' (blocked)' : '')));
      b.onclick = () => { if (hidden.has(m)) hidden.delete(m); else hidden.add(m); };
      lg.appendChild(b);
    }
  }
  if (titleKey !== title) { titleKey = title; $('h-total').textContent = 'All devices (' + title + ')'; $('h-dev').textContent = 'Per device (' + title + ')'; }
  const total = rows.map(r => [r.t - D, Object.values(r.d).reduce((s, x) => s + x[mi], 0)]);
  const series = macs.filter(m => !hidden.has(m)).map(m => ({name: m, color: colorOf(m), dash: blk[m] ? [6, 4] : [], pts: rows.map(r => [r.t - D, r.d[m] ? r.d[m][mi] : null])}));
  drawChart($('c-total'), [{name: 'All devices', color: css('--accent'), pts: total, fill: true}], unit);
  drawChart($('c-dev'), series, unit);
}

/* ---- animation loop: graphs ~25 fps, cards and tiles 10x per second ---- */
function frame(ts) {
  requestAnimationFrame(frame);
  if (!samples.length) return;
  const D = dispT();
  if (ts - tC > 100) { tC = ts; const list = view(D); tiles(list); if (tab == 'devices') renderCards(list); }
  if (tab == 'graphs' && ts - tG > 40) { tG = ts; drawGraphs(D); }
}

function mark(sel, f) { document.querySelectorAll(sel + ' button').forEach(b => b.className = b.dataset.f == f ? 'on' : ''); }
$('seg').onclick = e => { const f = e.target.dataset.f; if (!f) return; filter = f; mark('#seg', f); tC = 0; };
$('tabs').onclick = e => {
  const b = e.target.closest('button'); if (!b) return; tab = b.dataset.tab;
  document.querySelectorAll('#tabs button').forEach(x => x.className = x === b ? 'on' : '');
  $('tab-devices').style.display = tab == 'devices' ? '' : 'none'; $('tab-graphs').style.display = tab == 'graphs' ? '' : 'none'; tC = 0;
};
$('mseg').onclick = e => { const f = e.target.dataset.f; if (!f) return; metric = f; mark('#mseg', f); };
$('wseg').onclick = e => { const f = e.target.dataset.f; if (!f) return; win = +f; mark('#wseg', f); };
['c-total', 'c-dev'].forEach(id => { const cv = $(id); cv.onmousemove = e => { cv._hx = e.offsetX; }; cv.onmouseleave = () => { cv._hx = null; }; });
$('addblock').onclick = async () => { if (await act('block', $('mac').value.trim().toLowerCase())) $('mac').value = ''; };
$('clear').onclick = () => { samples = []; act('clear'); };
poll(); setInterval(poll, 500); requestAnimationFrame(frame);
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
        u = urlparse(self.path)
        if u.path == "/history":
            q = parse_qs(u.query)
            try:
                res = SAMPLER.history(float(q.get("since", ["-1"])[0]), int(q.get("gen", ["-1"])[0]))
            except ValueError:
                res = {"error": "bad request"}
            self.reply(500 if "error" in res else 200, json.dumps(res).encode())
        elif u.path in ("/", "/index.html"):
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
                SAMPLER.reset()
            else:
                set_blocked(body["mac"].lower(), self.path == "/block")
                SAMPLER.kick()
            self.reply(200, b'{"ok": true}')
        except Exception as e:
            self.reply(400, json.dumps({"error": str(e)}).encode())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between samples (raise it to save CPU)")
    a = ap.parse_args()
    SAMPLER = Sampler(a.interval)
    SAMPLER.start()
    print("Open http://%s:%d" % (a.bind, a.port))
    ThreadingHTTPServer((a.bind, a.port), Handler).serve_forever()
