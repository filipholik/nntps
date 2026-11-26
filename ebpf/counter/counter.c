// counter.c - count packets and bytes per receiving interface (XDP)
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

struct stats {
    __u64 packets;
    __u64 bytes;
};

/* key: ifindex of the receiving device; value: counters (one copy per CPU) */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_HASH);
    __uint(max_entries, 256);
    __type(key, __u32);
    __type(value, struct stats);
} pkt_stats SEC(".maps");

SEC("xdp")
int xdp_count(struct xdp_md *ctx)
{
    __u32 ifindex = ctx->ingress_ifindex;
    __u64 len = ctx->data_end - ctx->data;      /* frame length in bytes */

    struct stats *s = bpf_map_lookup_elem(&pkt_stats, &ifindex);
    if (s) {                                    /* per-CPU copy: no atomics needed */
        s->packets += 1;
        s->bytes += len;
    } else {
        struct stats init = { .packets = 1, .bytes = len };
        bpf_map_update_elem(&pkt_stats, &ifindex, &init, BPF_NOEXIST);
    }
    return XDP_PASS;                            /* observe only, never interfere */
}

char LICENSE[] SEC("license") = "GPL";
