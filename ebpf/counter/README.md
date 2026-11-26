# XDP per-device packet/byte counter with a web page

`counter.c` counts packets and bytes per receiving interface (key = ifindex, per-CPU hash map `pkt_stats`) and returns `XDP_PASS`, so traffic is not affected. `stats_server.py` reads the map with `bpftool` and shows it on a web page with live rates.

## Build
    sudo dnf install clang libbpf-devel bpftool   # Fedora
    make

## Load (one attachment per interface)
With bpfman (syntax as in the VM; newer versions split this into `load file --programs xdp:xdp_count` + `attach <id> xdp --iface ...`, check `bpfman load file --help`):

    sudo bpfman load file --path counter.o --name xdp_count xdp --iface s1-eth1 --priority 50
    sudo bpfman load file --path counter.o --name xdp_count xdp --iface s1-eth2 --priority 50

Without bpfman (quick test, generic mode):

    sudo ip link set dev s1-eth1 xdpgeneric obj counter.o sec xdp
    sudo ip link set dev s1-eth1 xdpgeneric off      # detach

## Run the web page
    sudo python3 server.py          # http://127.0.0.1:8000  (--bind / --port available)

## Test in Mininet
    mininet> h1 ping h2 -i 0.001
    mininet> iperf h1 h2

## Show eBPF map manually 
    sudo bpftool map dump name pkt_stats

## Notes
- Bytes are the frame length at the XDP hook (Ethernet header included, no FCS).
- Counters are per receiving interface (RX only), so attach the program to every port you want to see.
- Per-CPU map: each CPU updates its own copy without atomics; the script sums the CPUs.
- bpfman may create a separate `pkt_stats` map per load; the script sums all maps with that name.
- The script needs BTF (compiled with `-g`) or falls back to raw hex output; it needs root because of `bpftool`.
