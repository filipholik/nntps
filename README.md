# NNTPS Course Environment

Course environment for teaching SDN with **Floodlight** and **Ryu**, running in Docker so students don't need to build either controller natively.

This repo assumes a Fedora (44) XFCE VM. Everything below was tested on that setup. Other Fedora editions / spins might require some tweaking.   

## Repo layout

```
nntps/
├── config/
│   └── config-notes.txt       # rough description of VM changes made 
│   ├── init-vm-phase1.sh      # one-time VM setup (packages, Docker, SELinux, etc.)
│   └── init-vm-phase2.sh      # post-reboot setup (OVS, sanity checks, chmod)
├── docker/
│   ├── floodlight/            # Floodlight controller (default GUI)
│   │   ├── Dockerfile
│   │   └── compose.yml
│   ├── floodlight-webui/      # Floodlight controller + richer sb-admin2/DataTables web UI
│   │   ├── Dockerfile
│   │   └── compose.yml
│   └── ryu/
│       ├── Dockerfile
│       └── compose.yml
├── ryu/                       # Ryu 4.34 source, editable directly               
├── floodlight-start.sh
├── ryu-start.sh
├── mininet-start.sh
└── README.md
```

## One-time VM setup

Run once on a fresh VM:

```bash
cd config
./init-vm-phase1.sh
```

This installs VirtualBox Guest Additions, removes unneeded default apps, updates the system, installs course tooling (Docker, Mininet, Wireshark, BPF tools, VS Code), disables SELinux, and sets up group permissions for Docker/Wireshark. It ends by offering to reboot — say yes, since several of these changes (SELinux, group membership, kernel modules) only take effect after a reboot.

After rebooting, log back in and run:

```bash
cd nntps/config
./init-vm-phase2.sh
```

This starts Open vSwitch, runs a sanity check (SELinux status, Docker/Wireshark group membership, `docker compose` version, Mininet), and makes every `.sh` script in the repo executable.

If the sanity check reports anything missing, don't proceed until it's fixed — a missing group membership or a failed Docker install will cause confusing failures later, not obviously related to the actual cause.

## Running Floodlight

```bash
./start-floodlight.sh
```

Builds the image (first run only takes a while — subsequent runs use Docker's cache) and starts the controller **attached**: logs stream to your terminal, `Ctrl+C` stops it cleanly.

| | |
|---|---|
| Web UI | http://localhost:8082/ |
| REST API | http://localhost:8082/wm/core/controller/switches/json |
| OpenFlow (for Mininet) | port 6653 on the host |

Point Mininet at it:
```bash
sudo mn --controller=remote,ip=127.0.0.1,port=6653 --switch ovsk,protocols=OpenFlow13
```

Other options:
```bash
./start-floodlight.sh --rebuild   # force a clean rebuild (no cache)
./start-floodlight.sh --detach    # run in the background instead of attached
./start-floodlight.sh --stop      # stop a --detach'd container
./start-floodlight.sh --logs      # follow logs of a --detach'd container
```

This repo uses a **patched fork** of `floodlight-webui`, not the original — the original repo's port-statistics, hosts, and topology pages don't work against Floodlight 1.2's REST API (field-naming mismatches). If you fork/modify this setup further, see `docker/floodlight-webui/Dockerfile` for which fork/branch it builds from.

## Running Ryu

```bash
./start-ryu.sh
```

Runs `ryu.app.simple_switch_13` by default, attached (same behavior as Floodlight above — `Ctrl+C` stops it). Ryu's source lives at `vendor/ryu/` and is bind-mounted into the container, so **edits there take effect immediately on the next run** — no rebuild needed for Python changes to the framework itself or to any app under `vendor/ryu`.

```bash
./start-ryu.sh --app ryu.app.simple_switch_stp_13   # run a different app
./start-ryu.sh --gui                                # simple_switch_13 + GUI topology viewer
./start-ryu.sh --gui --app <module.path>            # GUI + a specific app
./start-ryu.sh --version                            # sanity-check the image
```

With `--gui`, open http://127.0.0.1:8080/ once it's running.

Point Mininet at it:
```bash
sudo mn --topo tree,3 --controller=remote,ip=127.0.0.1,port=6653 --switch ovs,protocols=OpenFlow13
```

## ⚠️ Port collisions between Floodlight and Ryu

Both setups claim host port **8080** (web UI / GUI) and overlapping OpenFlow ports. **Only run one controller at a time.** If starting one fails with a "port already in use" or "address already allocated" error, stop the other first (`Ctrl+C` if it's running attached, or `./start-floodlight.sh --stop` if it was started with `--detach`).

## Troubleshooting

**"Requested unknown parameter" errors in the Floodlight web UI** — this was a real issue during development: the original `floodlight-webui` project's JS expects different JSON field names than Floodlight 1.2's REST API actually returns. It's fixed in this repo's fork, but if you point the Dockerfile at a different webui source, expect this to resurface.

**Docker build fails with "git: command not found" or exit code 127** — the build container is missing `git`. Check the `apt-get install` line in the relevant `Dockerfile`.

**Docker build fails with exit code 128 on a `git clone` step** — usually means the referenced branch/tag doesn't actually exist on the target remote. Check with:
```bash
git ls-remote --tags <repo-url>
```

**Scripts aren't executable after cloning** — git doesn't always preserve the executable bit through certain clone/copy operations. Fix with:
```bash
chmod +x *.sh config/*.sh
```
(`init-vm-phase2.sh` does this automatically as part of its sanity check.)

**Docker permission denied** — you likely haven't logged out/in (or rebooted) since `init-vm-phase1.sh` added you to the `docker` group. Group membership changes require a new login session to take effect.

**bpfman crashes with a sigstore/TUF "Invalid key ID" panic on `bpfman load`** — this is already worked around by `init-vm-phase1.sh` (which disables Cosign image-signature verification, since this course only loads local `.o` files, never signed OCI images). If you see this on a VM that skipped phase 1, or after a `bpfman` package update, check `/etc/bpfman/bpfman.toml` exists with `[signing]` disabled.

## Note: rebuilding from source

Both Floodlight and Ryu are pinned to specific, tested versions rather than tracking upstream:

- Floodlight: fork of `floodlight/floodlight`, tag `v1.2`
- Floodlight webui: fork of `floodlight/floodlight-webui`, patched for v1.2's REST API field names
- Ryu: vendored source at `vendor/ryu/`, based on tag `v4.34`

If you need to update any of these, do it deliberately and re-test the full `start-*.sh` flow — small REST API or dependency changes between versions have caused real breakage in this setup before (see the webui note above).
