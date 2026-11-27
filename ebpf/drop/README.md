# XDP Drop eBPF Program

A simple XDP eBPF program managed via `bpfman` and inspected with `bpftool`.

## Prerequisites

* `clang` and LLVM toolchain installed
* `bpfman` daemon running
* `bpftool` installed for map inspection (`sudo dnf install bpftool`) 
* Mininet running (to provide the `s1-eth1` network interface)

## Usage

### 1. Compile the eBPF Program

```bash
clang -O2 -g -target bpf -c xdp_drop.c -o xdp_drop.o
```

### 2. Load the eBPF Program

Ensure Mininet is running so the `s1-eth1` interface exists, then attach the XDP program:

```bash
sudo bpfman load file \
    --path ./xdp_drop.o \
    --name xdp_drop_prog \
    xdp --iface s1-eth1 --priority 50
```

### 3. Verify Loaded Programs

List active eBPF programs and note the assigned `<id>`:

```bash
sudo bpfman list
```

### 4. Inspect eBPF Map Content

Dump the contents of a specific eBPF map by replacing `NAME` with your map's name:

```bash
sudo bpftool map dump name NAME
```

### 5. Unload the eBPF Program

Remove the program when finished using its `<id>` from `bpfman list`:

```bash
sudo bpfman unload <id>
```
