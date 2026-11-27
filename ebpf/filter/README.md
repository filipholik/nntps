# XDP MAC filter with web page

 `macfilter.c` + `server.py`: packets and bytes per MAC address, block / allow buttons (section at the end)

## Build
    sudo dnf install clang libbpf-devel bpftool   # Fedora
    make

## MAC filter (filter.c + server.py)
The program learns every source MAC into the map `mac_stats` (packets, bytes) and drops frames from or to any MAC listed in the map `blocked`. The web page lists the MACs seen, with rates, a dropped-frame counter and a Block / Allow button; a MAC that has not appeared yet can be typed in and blocked.

    make
    sudo bpfman load file --path filter.o --name xdp_macfilter xdp --iface s1-eth1 --priority 50
    sudo bpfman load file --path filter.o --name xdp_macfilter xdp --iface s1-eth2 --priority 50
    sudo python3 server.py            # http://127.0.0.1:8000

## Test

Recommended topology: `sudo mn --topo=tree,3,2 --mac` (s1 is the root switch)

Test in Mininet: `h1 ping h2`, block h2's MAC in the browser (`h2 ifconfig` shows it): the ping stops and "Dropped" grows; click Allow and it resumes.

- Only frames that reach an interface with the program attached are seen or dropped: attach it to every port.
- Each load of the program may get its own maps; the server reads and updates all of them.
- Counters show frames seen at the hook (blocked ones included), "Dropped" counts the frames discarded.
- `mac_stats` is an LRU map: if many (spoofed) MACs appear, the oldest entries are evicted. Blocked MACs are kept in `blocked`.
- Multicast/broadcast addresses cannot be blocked. POST requests must be JSON, so other web sites cannot trigger a block.
- Manual check: `sudo bpftool map dump name blocked`
