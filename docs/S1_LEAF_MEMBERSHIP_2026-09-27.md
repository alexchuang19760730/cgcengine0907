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
| `:3937` | `mk` = `ffn_moe_missmask` | ⛔ ~~是（leaf）~~ **否（GET_ROWS 的 op 结果）** | ⛔ **本行已撤回（§8）**：正确，**不动** |

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
2. **⛔ 已于 2026-09-28 撤回（理由与实测证据见 §8）：`ffn_moe_missmask` 不是 leaf。**
   原文主张它是 leaf，因而「在 `CGC_MISS_MASK=1` 臂上会被同一个 nodes-only 测试拒绝，
   MISSMASK readback 同样整段跳过」。实情相反：它是一条 `ggml_get_rows` 的 op 结果，
   nodes-only 测试对它**是正确的**，而该 readback 一直在输出（§8.2 的 78 行）。
   §5 表格里对应那一行同样撤回——**不要**把它换成 `cgc_tensor_in_graph`（§8.3）。
3. **不改 `cgc_node_in_graph` 本身。** 它必须保持 nodes-only——现有调用点依赖该语义。
   修复方式是让它有一个语义更宽的兄弟函数，而不是改它。

---

## §8 追补更正（2026-09-28）：§7.2 的「同类未修」**不成立**，`ffn_moe_missmask` 不是 leaf

> 本节的触发是一道指令：把 §7.2 那一行「用同一种修法补上」。查下去发现**要修的不是代码，是 §7.2 本身**。
> §7.2 与其在 §5 表格里的那一行，自此**撤回**；本文其余部分不受影响。

### 8.1 §7.2 引用的那一行，测试是**对的**

`llama-context.cpp:4021`（§7.2 写作 `:3937`，行号随 09-26／09-27 的插入而位移）：

```c
if (!cgc_node_in_graph(gf, mk) || !cgc_node_in_graph(gf, idc)) {
    continue;
}
```

`mk` = `cache_missmask_tensors[il]` = `ffn_moe_missmask`，而它在 `llama-graph.cpp:2422` 的构造是：

```c
ggml_tensor * vmask = ggml_get_rows(ctx0, valid_table, ids_flat);  // ← GET_ROWS 是一个 op
ggml_set_output(vmask);
cb(vmask, "ffn_moe_missmask", il);
ggml_build_forward_expand(gf, vmask);                             // ← 09-26「step 2 REBUILD · FIX #1」加的
```

`GET_ROWS`（`:2422`）有 producer op，而 09-26 那次 rebuild 之所以补上 `ggml_build_forward_expand`（`:2446`），
正是因为**没有 expand 的节点不会被排程**（该处注释记着实测：
`GGML_ASSERT(buffer_id >= 0) failed ggml-alloc.c:623`，rc=-6，首次启动即死）。
⇒ `vmask` **在 `gf->nodes` 里**，`cgc_node_in_graph` 对它回 **true**。

这一段里真正的 leaf 是它的**输入** `valid_table`（`ggml_new_tensor_2d` ＋ `set_output` ＋
`cb("ffn_moe_valid")`，`llama-graph.cpp:2415-2421`），而 `valid_table` 的消费者**早就**用了宽函数：
`:3896` 是 `cgc_tensor_in_graph(gf, vt, &vt_where)`，并把 `vt_where == 1` 计成 `n_as_leaf`。

⇒ §7.2 把 **`ffn_moe_valid`（leaf）** 与 **`ffn_moe_missmask`（op 结果）** 混为一谈。
名字相近（`valid` vs `missmask`）不是理由：两者的建構方式不同，而判定 leaf 与否只取决于此。

### 8.2 用行为判，不用档案判（本 repo 自己的规矩）

那条 readback **一直在输出**。`CGC_MISS_MASK_DBG=1` 的两份 log 各有 **78 行** `MISSMASK il=`：

```
Backup/cgc_logs/llama_server_20260928_124618.log   78 行
Backup/cgc_logs/llama_server_20260928_125503.log   78 行
范例：MISSMASK il=1 step=1 nsel=16 misses=8 exps: 193 229 249 220 181 161 250 212
```

而这两份 log 就是 `docs/S2_OVERLAP_EXPERIMENT_2026-09-28.md` §11 引用的那两份
（`:245`、`:313`）—— 即**同一批 run** 产出了 S2 doc §11.5 的答案（`ids_src_valid=1 × 78`）。
**一段整段被跳过的 readback 印不出 78 行。**

### 8.3 因此：不要动 `:4021`

`cgc_tensor_in_graph` 是 `cgc_node_in_graph` 的**严格超集**，所以把 `mk` 换过去对结果只是 no-op，
却会**抹掉这两个函数存在的理由**（nodes vs leafs 的区分）。全站呼叫点审计（09-28 现况）：

| 行 | 对象 | 是 leaf？ | 用的函数 | 判定 |
|---|---|---|---|---|
| `:3896` | `vt` = `ffn_moe_valid` 的 capture | **是** | `cgc_tensor_in_graph` ＋ `where` | ✅ 正确 |
| `:4021` | `mk` = `ffn_moe_missmask` | 否（GET_ROWS） | `cgc_node_in_graph` | ✅ 正确 |
| `:4021` | `idc` = `ffn_moe_ids_cont` | 否（`ggml_cont`） | `cgc_node_in_graph` | ✅ 正确 |
| `:4155` | `s` = `ffn_moe_slots` | 否（GET_ROWS） | `cgc_node_in_graph` | ✅ 正确 |
| `:4189`／`:4193` | `rm`／`tb` = remap leaf／slot table | **是** | `cgc_tensor_in_graph` | ✅ 09-27 已修 |

⇒ **09-27 的修复把这个家族关干净了，没有残留的第二条死探针。**

### 8.4 这条更正为什么比它看起来贵

错的方向刚好**会诱导一个不必要的改动**：把一个正确的测试换成超集 —— 结果是 no-op，
但 diff 看起来像「修好了」，而「nodes vs leafs」正是本 bug 的语义被抹平。
第二个代价是它会让 3a／3b 那条已判「可放生产」的 miss-mask 判词**看起来像空号**——它不是。

> 判准：**一个「探针死了」的句子，必须与「探针活着但没有事件」的句子长得不一样。**
> 本节两句都给了证据：§8.1 是静态的（建构方式），§8.2 是动态的（78 行输出）。
