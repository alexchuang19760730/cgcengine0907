#!/usr/bin/env python3
"""Minimal streaming GGUF header parser (no numpy dependency).

Purpose (why this exists): 'expert_index built: 30720 vs 31488' has to be explained
from the model file itself, and installing numpy just to import gguf-py is not worth it.
Prints: expert-tensor census grouped by layer/kind, plus every nextn/MTP tensor name.
"""
import struct
import sys
from collections import Counter

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    'models/gguf/Nail-Qwen3.6-35B-A3B-MTP-UD-IQ3_XXS-denseIQ4X.gguf'


class Reader:
    """Self-refilling buffer so any field can be read with struct.unpack_from."""

    def __init__(self, path, chunk=1 << 22):
        self.f = open(path, 'rb')
        self.chunk = chunk
        self.buf = b''
        self.base = 0          # file offset corresponding to buf[0]

    def ensure(self, n, pos):
        """Make sure [pos, pos+n) is inside the buffer; returns local index."""
        need = pos + n
        while self.base + len(self.buf) < need:
            more = self.f.read(max(self.chunk, need - self.base - len(self.buf)))
            if not more:
                raise EOFError(need)
            self.buf += more
        return pos - self.base

    def u64(self, pos):
        i = self.ensure(8, pos)
        return struct.unpack_from('<Q', self.buf, i)[0]

    def u32(self, pos):
        i = self.ensure(4, pos)
        return struct.unpack_from('<I', self.buf, i)[0]

    def string(self, pos):
        n = self.u64(pos)
        i = self.ensure(n, pos + 8)
        return self.buf[i:i + n].decode('utf-8', 'replace'), pos + 8 + n

    def string_of_len(self, pos, n):
        i = self.ensure(n, pos)
        return self.buf[i:i + n]


def read_value(r, pos, vtype):
    if vtype in (0, 1):
        i = r.ensure(1, pos); return r.buf[i], pos + 1
    if vtype == 7:
        i = r.ensure(1, pos); return bool(r.buf[i]), pos + 1
    if vtype in (2, 3):
        i = r.ensure(2, pos); return struct.unpack_from('<h', r.buf, i)[0], pos + 2
    if vtype in (4, 5):
        i = r.ensure(4, pos); return struct.unpack_from('<i', r.buf, i)[0], pos + 4
    if vtype == 6:
        i = r.ensure(4, pos); return struct.unpack_from('<f', r.buf, i)[0], pos + 4
    if vtype == 10:
        return r.u64(pos), pos + 8
    if vtype == 11:
        i = r.ensure(8, pos); return struct.unpack_from('<q', r.buf, i)[0], pos + 8
    if vtype == 12:
        i = r.ensure(8, pos); return struct.unpack_from('<d', r.buf, i)[0], pos + 8
    if vtype == 8:
        return r.string(pos)
    if vtype == 9:
        etype, n = struct.unpack_from('<IQ', r.buf, r.ensure(12, pos))
        pos += 12
        vals = []
        for _ in range(n):
            v, pos = read_value(r, pos, etype)
            vals.append(v)
        return vals, pos
    raise ValueError('unknown value type %d' % vtype)


def main():
    r = Reader(PATH)
    magic = r.string_of_len(0, 4)
    ver = r.u32(4)
    pos = 8
    tensor_count = r.u64(pos); pos += 8
    kv_count = r.u64(pos); pos += 8

    meta = {}
    for _ in range(kv_count):
        key, pos = r.string(pos)
        vtype = r.u32(pos); pos += 4
        val, pos = read_value(r, pos, vtype)
        meta[key] = val

    tensors = []
    for _ in range(tensor_count):
        name, pos = r.string(pos)
        ndim = r.u32(pos); pos += 4
        dims = []
        for _ in range(ndim):
            d = r.u64(pos); pos += 8
            dims.append(d)
        ttype = r.u32(pos); pos += 4
        off = r.u64(pos); pos += 8
        tensors.append((name, dims, ttype, off))

    print('magic=%s ver=%d  n_kv=%d  n_tensors=%d' % (magic, ver, kv_count, tensor_count))
    for k in ('general.architecture', 'general.name',
              '%s.block_count' % meta.get('general.architecture', '?'),
              '%s.expert_count' % meta.get('general.architecture', '?'),
              '%s.nextn_predict_layers' % meta.get('general.architecture', '?')):
        if k in meta:
            print('  %-46s = %s' % (k, meta[k]))

    # names referencing nextn / MTP block
    nextn = [t for t in tensors if 'nextn' in t[0].lower()]
    print('\n--- nextn/MTP tensors: %d ---' % len(nextn))
    for name, dims, ttype, off in nextn[:40]:
        print('   %-64s %s' % (name, dims))

    # expert census exactly as the loader sees it: contains "_exps" and "blk."
    exps = [t for t in tensors if '_exps' in t[0] and 'blk.' in t[0]]
    print('\n--- expert tensors matching loader pattern: %d ---' % len(exps))
    per_layer = Counter()
    per_kind = Counter()
    ne2_seen = Counter()
    for name, dims, ttype, off in exps:
        il = int(name.split('blk.')[1].split('.')[0])
        if 'ffn_gate_up_exps' in name:
            kind = 'gate_up'
        elif 'ffn_up_exps' in name:
            kind = 'up'
        elif 'ffn_down_exps' in name:
            kind = 'down'
        elif 'ffn_gate_exps' in name:
            kind = 'gate'
        else:
            kind = 'UNMATCHED->skipped'
        if kind == 'UNMATCHED->skipped':
            per_kind[kind] += 1
            continue
        per_layer[il] += 1
        per_kind[kind] += 1
        ne2_seen[dims[2]] += 1

    layers = sorted(per_layer)
    print('  layers with exps: %d  (%d..%d)' % (len(layers), layers[0], layers[-1]))
    print('  kind counts      :', dict(per_kind))
    print('  expert dim ne[2] :', dict(ne2_seen))
    print('  per-layer counts : first=%s  last=%s' % (
        [(l, per_layer[l]) for l in layers[:2]],
        [(l, per_layer[l]) for l in layers[-3:]]))
    total_entries = sum(per_layer[l] * ne2_seen.most_common(1)[0][0] if ne2_seen else 0
                        for l in layers)
    print('  => index entries if unshrunk = per_layer_cap * expert_count = %d'
          % (len(layers) * per_layer[layers[0]] * ne2_seen.most_common(1)[0][0]))
    print('     (observed at runtime: OFF 30720 / ON 31488)')

    other_exps = [t[0] for t in tensors if '_exps' in t[0] and 'blk.' not in t[0]]
    print('\n--- tensors with _exps but no "blk.": %d ---' % len(other_exps))
    for n in other_exps[:10]:
        print('   ', n)


if __name__ == '__main__':
    main()
