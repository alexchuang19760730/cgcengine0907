# S1「决定性 readback」为何一直是死的 —— leaf membership 的落地修复

日期：2026-09-27
范围：`src/llama.cpp/src/llama-context.cpp`（本线）
性质：**修复一个静默失效的探针**，不是性能改动，不是数值改动。

---

## §1 症状

`llama-context.cpp` 的 S1 POST 区块（CGC 2026-09-15 引入，注释自称
**"the decisive readback"**）里，有两个 membership 判断：

```c
// 修复前
if (!cgc_node_in_graph(gf, rm) || !cgc_is_i32_n(rm, ntot) ||
        rm->ne[0] != s->ne[0] || rm->ne[1] != s->ne[1]) {
    rm = nullptr;                                   // ← 永远走到这里
}
if (!cgc_node_in_graph(gf, tb) || !cgc_is_i32_n(tb, tb->ne[1])) {
    tb = nullptr;                                   // ← 永远走到这里
}
```

`rm = ffn_moe_topk_remap`（`llama-graph.cpp:2508` / `:2555`），
`tb = ffn_moe_slot_table`（`llama-graph.cpp:2266`）。两者都在**同一个 if 的下一行**被解引用：

```c
int ids_src_valid = 0;
for (int32_t v : ibuf) {
    if (v < 0 || v >= (int32_t)(tb != nullptr ? tb->ne[1] : 0)) {   // tb 恒 null ⇒ 上界恒 0
        ids_src_valid = 0;
        break;
    }
    ids_src_valid = 1;                                              // ← 永远不可达
}
...
if (ids_src_valid == 1 && !tbuf.empty()) { ... n_gather_mis ... }    // ← 永远不执行
```

⇒ `ids_src_valid` 恒 0、`n_gather_mis` 恒 -1。**整段 differential 是死代码。**

---

## §2 机制：`cgc_node_in_graph` 只走 `nodes`

`cgc_node_in_graph`（`:3405`）遍历 `ggml_graph_n_nodes(gf)` 后返回。
而 `ffn_moe_topk_remap` / `ffn_moe_slot_table` 的构建方式是：

```c
ggml_tensor * remap = ggml_new_tensor_2d(ctx0, GGML_TYPE_I32, n_expert_used, n_tokens);
ggml_set_output(remap);                      // 无 producer op
cb(remap, "ffn_moe_topk_remap", il);
ggml_build_forward_expand(gf, remap);
```

没有 producer op ⇒ `ggml_visit_parents` 把该 tensor 归入 `cgraph->leafs`
（ggml-impl.h:337 的 "tensors with constant data"），**永远不出现在 `nodes` 里**。
⇒ nodes-only 测试对这两者**逐层返回 false**。

同样的构造在 `ffn_moe_valid`（`:2420`，注释自陈 "host-writable leaf"）上也成立。

---

## §3 证据（测量，不是论证）

`828f4d1c2` 在图上加了 `cgc_tensor_in_graph` 并留下实测数据
（`Backup/phase_decomp/g2_provenance_run3`）：

```
n_leaf=39   wrote=0   skip_not_in_graph=39   skip_null=0
```

- `skip_null=0` ⇒ 39 个 leaf **全部已分配**（它们确实在图里）
- `skip_not_in_graph=39` ⇒ 而 nodes-only 测试仍对全部 39 个说「不在图里」

两条并存只能有一个解释：**测试漏了 leafs，而不是 leaf 不在图里**。
该 commit 的注释因此写下了「`ffn_moe_rn_mask` 与 slot table（`:3919`/`:3923`）用同一个测试，
**至今仍被静默强制为 nullptr**」——但**没有把它接上**。

---

## §4 影响范围：是「没有数字」，不是「数字错了」

重要区分。这段 readback 本该回答的问题是
**「Metal 的 GET_ROWS 实际产出什么」**（vs. 主机侧 leaf 写的期望值）。
它没跑过，所以：

- 不存在「某个具体百分比是错的」这类结论；
- 但**任何声称来自这条路径的结论都不存在** —— 它从来没有输出过一行。

对照：`scripts/check/decode_sweep.py` 报出的 **G2 = 42.93%**
（`SPEED_ACCEPTANCE_GATE_2026-09-26.md` §9.8）**来自另一条路径**，不受本次修复影响。

进入条件：`static const bool cgc_s1_post = getenv("CGC_S1_DBG") != nullptr;`（`:4039`）
⇒ 生产 / gate 默认臂不进入这段代码。修复对默认臂的运行时行为**零影响**。

---

## §5 修复

把两处 `cgc_node_in_graph` 换成 `cgc_tensor_in_graph`（`:3437`）：

```c
if (!cgc_tensor_in_graph(gf, rm) || !cgc_is_i32_n(rm, ntot) || ...) { rm = nullptr; }
if (!cgc_tensor_in_graph(gf, tb) || !cgc_is_i32_n(tb, tb->ne[1]))   { tb = nullptr; }
```

`cgc_tensor_in_graph` 是 `cgc_node_in_graph` 的**严格超集**（先查 `nodes`、再查 `leafs`），
且 `where` 参数改为带默认值 `= nullptr`，使只要 yes/no 的调用点不必传它。
因此：

- 对 op 结果，接受度**不会变严**（超集，不是替代）；
- 只有 leaf 从「错误地拒绝」变成「正确地接受」；
- 两个 leaf 都是 `ggml_set_output`，post-synchronize 时主机缓冲仍存活，
  这与上面 `CGC-REMAP-POST` 直接读 `r->data` 是同一性质。

其余 `cgc_node_in_graph` 调用点保持不动：

| 位置 | 对象 | 是否 leaf | 处置 |
|---|---|---|---|
| `:4071` | `s` = `ffn_moe_slots` 输出 | 否（GET_ROWS 的 op 结果） | 正确，**不动** |
| `:3937` | `mk` = `ffn_moe_missmask` | 是（leaf） | **同类未修**，见 §7 |

---

## §6 验证

### 6.1 D5 默认臂 —— 未被影响（应该的）

`--tag leaffix-0927`，新 `libllama.0.dylib=621b9a5b`：

```
GATE leaffix-0927: PASS   M1(bit-identical)=9/9  M2(argmax)=9/9  M3(topk)=9/9  n=9
cross-tab: {'num_eq_dec_eq': 9, 'num_eq_dec_ne': 0, 'num_ne_dec_eq': 0, 'num_ne_dec_ne': 0}
```

这段代码在 `getenv("CGC_S1_DBG")` 之下，gate 默认臂不进入，所以 PASS 是**预期**而非证明。

### 6.2 leaf 路径是否真的活了 —— 是

第一次 `CGC_S1_DBG=1`（不开 `CGC_SLOT_TABLE_GPU`）：
`CGC-S1: POST` **0 行**。原因不是 leaf-blind，而是更上游——`ffn_moe_slots` 由
`ggml_get_rows(ctx0, slot_table, ...)` 派生，而 `slot_table` 只在 `CGC_SLOT_TABLE_GPU=1`
时才构建（`llama-graph.cpp:2266`）⇒ `cache_slots_out_tensors` 为空，POST 区块遍历空集。

第二次 `CGC_S1_DBG=1 CGC_SLOT_TABLE_GPU=1`

`Backup/cgc_logs/llama_server_20260927_021409.log` ⇒ **120 行**，且：

| 字段 | 修复前 | 修复后 |
|---|---|---|
| `table_data` | `0x0`（tb 被钉死为 null） | `0x6237c0040` 非 null |
| `ids_src_valid` | **恒 0**（上界 `tb->ne[1]` 取不到，恒 0） | **120/120 全部 = 1** |
| `gather_vs_table` | 不执行 | **120/120 全 = 0** |

样本（il=1，ntok=2，n_expert=256）：

```
CGC-S1: POST il=1 ntok=2 n_expert=256 gather=[3 105 6 7 5 106 2 84 ...]
  idx=[193 105 229 249 220 106 181 84 ...]
  same=0 ids_src_valid=1 gather_vs_table=[0]   table_data=0x6237c0040
```

旁证闭合：同一份 log 里 hook 侧 `EXPECT-pool il=1 ... ids=[193->3 105->105 229->6 ...]`
正好是 `table[193]=3, table[105]=105, table[229]=6` ⇒ 与 gather 的 `3 105 6` 逐位对应。
两条独立路径（encode 前的 hook 期望值、post-sync 的 GPU 实际产出）指向同一张表。

### 6.3 这条 readback 第一次给出了它的答案

`llama-context.cpp:4034` 的注释写下的是一个待定问题：

> `equal -> the GPU path is correct and every id_oob line was a probe artifact;
> the logits divergence then has a different cause`

`gather_vs_table` 120/120 全 0 ⇒ **取这半支**：Metal 的 GET_ROWS 产出与 slot table 一致。
（`same=0` 那半支仍不可判——`leaf=[n/a]`，主机 leaf 未写值，leaf path 未启用，与本次修复无关。）

---

## §7 明确不宣称 / 未做

1. **不宣称任何新数字。** 本次只让一段既有探针重新可能执行；它的输出**第一次**出现，
   本身不是「测出了回退」，也不是「证明了正确性」。
2. **`:3937` 同类未修。** `ffn_moe_missmask` 同样是 leaf，在 `CGC_MISS_MASK=1` 臂上
   会被同一个 nodes-only 测试拒绝，MISSMASK readback 同样整段跳过。
   这次刻意不碰：它不在 gate 默认臂上，且 828f4d1c2 的注释已经把 blast radius
   限定在「这一个 bug」，本次也不扩大。
3. **不改 `cgc_node_in_graph` 本身。** 它必须保持 nodes-only——现有调用点依赖该语义。
   修复方式是让它有一个语义更宽的兄弟函数，而不是改它。
