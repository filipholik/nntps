// filter.c - per-source-MAC counters plus a MAC blocklist (XDP)
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <bpf/bpf_helpers.h>

struct stats   { __u64 packets; __u64 bytes; };
struct verdict { __u64 dropped; };

/* A MAC address is stored as a number: aa:bb:cc:dd:ee:ff -> 0xaabbccddeeff */

/* Learned devices: key = source MAC, value = counters (one copy per CPU).
 * LRU: a MAC flood cannot fill the map, the oldest entries are evicted. */
struct {
    __uint(type, BPF_MAP_TYPE_LRU_PERCPU_HASH);
    __uint(max_entries, 1024);
    __type(key, __u64);
    __type(value, struct stats);
} mac_stats SEC(".maps");

/* Blocklist, filled from userspace: key = MAC, value = dropped-frame counter */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 256);
    __type(key, __u64);
    __type(value, struct verdict);
} blocked SEC(".maps");

static __always_inline __u64 mac_to_u64(const __u8 *m)
{
    return ((__u64)m[0] << 40) | ((__u64)m[1] << 32) | ((__u64)m[2] << 24) |
           ((__u64)m[3] << 16) | ((__u64)m[4] << 8) | (__u64)m[5];
}

SEC("xdp")
int xdp_macfilter(struct xdp_md *ctx)
{
    void *data     = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;
    struct ethhdr *eth = data;

    if ((void *)(eth + 1) > data_end)           /* too short: not our business */
        return XDP_PASS;

    __u64 src = mac_to_u64(eth->h_source);
    __u64 dst = mac_to_u64(eth->h_dest);
    __u64 len = data_end - data;

    /* 1. learn + count every frame by source MAC */
    struct stats *s = bpf_map_lookup_elem(&mac_stats, &src);
    if (s) {
        s->packets += 1;
        s->bytes += len;
    } else {
        struct stats init = { .packets = 1, .bytes = len };
        if (bpf_map_update_elem(&mac_stats, &src, &init, BPF_NOEXIST) < 0) {
            s = bpf_map_lookup_elem(&mac_stats, &src);  /* another CPU was faster */
            if (s) {
                s->packets += 1;
                s->bytes += len;
            }
        }
    }

    /* 2. drop frames from or to a blocked MAC */
    struct verdict *v = bpf_map_lookup_elem(&blocked, &src);
    if (!v)
        v = bpf_map_lookup_elem(&blocked, &dst);
    if (v) {
        __sync_fetch_and_add(&v->dropped, 1);
        return XDP_DROP;
    }
    return XDP_PASS;
}

char LICENSE[] SEC("license") = "GPL";
