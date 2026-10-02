# R6-DIRTY · 方案 B（per-layer 提交拆分 / 逐层 ensure）实现设计书

> 日期：2026-10-01 · 线 A · 配套
> - a10 负结果（P1 预测死）见 `.workbuddy/memory/2026-10-01.md` §EN- a10 结果
> - (i) 提交后校正 pass 设计书（已判死，因 fused-graph 错误 propagate）`docs/R6_POSTSUBMIT_CORRECTION_DESIGN_2026-10-01.md`
> - 方案 C（全专家常驻）显存实测：**OOM，16GB 盒子物理不可行**（见 §0）
> 目标臂：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1`（M1 0/9，placeholder=250582，每层 113/256 专家 `e%ns` 错 slot）

---

## 0. 方案 C 实测结论（先说死，免得再纠结）

直接用 `llama-bench -expert-cache 15375000000`（256/layer 所需 ≈14.3 GiB，由 `slots/layer = clamp(bytes/(max_layer*per_slot), 8, 256)`，当前 8GiB→143/layer 推得）起最小负载：

```
ggml_metal_synchronize: error: command buffer 1 failed with status 5
error: Insufficient Memory (00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)
CGC-METAL-FAIL: command buffer 1 failed with status 5 (compute #41, fail-stop)
RC=134 (SIGABRT，引擎拒绝返回 stale 输出)
```

- 14.3 GiB pool + 模型 ~12.2 GiB Metal 常驻 = 26.5 GiB 工作集 >> 16GB 盒子的 ~11.4 GiB Metal 上限（recommendedMaxWorkingSetSize）。
- **结论：方案 C 在 16GB 盒子上不可行，无需再议。** R6-DIRTY 只剩方案 B 类（逐层 ensure）一条路。

---

## 1. 根因（代码铁证）

`graph_compute` 把 41 层融成一张 `gf` 一次提交（`ggml_backend_sched_graph_compute_async`，llama-context.cpp:4112）。提交前 pre-submit 写表块（:3902-3940）把非驻留专家写成 `v = e % ns`——这是 placeholder 来源。

但 **async 路径（CGC_OA_ASYNC）能正确（M1 9/9）**，靠的是 ggml 调度器的**逐段提交 + 逐层 hook**：

- ggml-backend.cpp:1792-1900 的 DEFAULT（correct）分支：把图按 `ffn_moe_argsort-*` 节点切成 41 段，**每段先 WAIT 上一段完成 → 触发 top-k hook（`callback_eval`）→ 再 SUBMIT 本段**。
- 该 hook（`expert_cache_on_topk`，在 llama-context.cpp:6188）做两件事：
  1. 算出本层路由到的专家集合 → `cache_step_union` → `llama_expert_cache_prefetch_slot` **ensure 驻留**；
  2. 把 `ffn_moe_topk_remap` 叶子（mul_mat_id 实际消费的 slot 索引）**重写成本层正确 slot**（不再 `e%ns`）。
- 大段注释（:1792-1810）明说：必须 WAIT 完再 fire hook 再 submit，否则 remap 缓冲区竞态 → stale remap → 垃圾输出。

**SEG_BATCH 怎么坏的**——ggml-backend.cpp:1775-1792：

```cpp
static const bool cgc_seg_batch = getenv("CGC_SEG_BATCH") != nullptr;
if (cgc_seg_batch) {
    enum ggml_status ec = ggml_backend_graph_compute_async(split_backend, &split->graph);
    if (ec != GGML_STATUS_SUCCESS) return ec;
    ggml_backend_synchronize(split_backend);
    return GGML_STATUS_SUCCESS;   // ← 跳过上面 41 段 wait→hook→submit 循环
}
```

注释原话：*"No hook is fired, so no fill happens and ids stay stale -> output is WRONG (diagnostic only, never a deliverable arm)."*

⇒ **SEG_BATCH 是为了量「单提交 overhead」而故意关掉逐层 ensure 的诊断臂**。它的 M1 0/9 不是 bug，是设计取舍。要正确，只需**恢复那段已经被 async 路径证明正确的逐层 ensure 循环**。

---

## 2. 修复方案（最小改动、复用 proven 代码）

**核心 diff：删除 ggml-backend.cpp:1775-1792 的 `cgc_seg_batch` early-return 捷径。** 删除后，SEG_BATCH 臂落入同文件的 DEFAULT 41 段 `wait→callback_eval→submit` 循环，与 async 路径共用同一套逐层 ensure 机制。

预期效果：
- 每段 submit 前，本层 hook 已 `ensure` 本层路由到的专家驻留 + 重写 `ffn_moe_topk_remap` 叶子 → mul_mat_id 从**正确 slot** gather → 不再 `e%ns` → placeholder 归零 → M1 9/9。

### 2.1 必须保留的防御（来自现有注释）
- **stale-remap 竞态屏障**：DEFAULT 循环在 fire hook 前 `ggml_metal_get_cgc_done` 轮询等待上一段完成（:1818-1824 区域）。恢复循环即自动继承，不要改动这段 WAIT 逻辑。
- **诊断用途不丢**：SEG_BATCH 原本用来量「单提交省下的串行开销」（step 79.2ms = wait 66.3 + cb 6.1 + submit 4.2）。恢复后该诊断消失。建议：把「单提交 overhead 量测」改挂到一个**独立旗标**（如 `CGC_SEG_BATCH_DIAG`），默认关闭；SEG_BATCH 默认走正确路径。这样 R6-DIRTY 的修复与开销量测互不干扰。

### 2.2 改动规模估计
- 删除 ~17 行 early-return（:1775-1792）。
- 新增 ~3 行把诊断迁移到独立旗标（可选，但建议）。
- **零新逻辑**：逐层 ensure / remap 重写 / 竞态屏障全部复用 async 路径既有实现。

---

## 3. 关键风险与验收前必查（按优先级）

### 🔴 R1（最高）：`CGC_SLOT_TABLE_GPU=1` 下 hook 是否真的重写 remap 叶子
- a8 曾发现：`expert_cache_on_topk` 内部在 **GPU-table 模式有 early-return**（"SEG_BATCH 下不呼叫這個 hook" 部分源于此）。本臂带 `CGC_SLOT_TABLE_GPU=1`。
- 恢复循环让 hook **被调用**了，但 hook 内部若因 GPU-table 而早退、不写 remap 叶子 → 仍走 `e%ns` → M1 仍 0/9。
- **验收前必做**：在 `CGC_SLOT_TABLE_GPU=1` 下确认 `expert_cache_on_topk` 完整执行 remap 重写（stderr 应有 `CGC-SEG-RECOMPUTE`/remap 相关行，且不再有 GPU-table 早退日志）。若仍早退，需先在 hook 内修 GPU-table 分支（属独立小修复，不在本方案主 diff 内）。

### 🟡 R2：`CGC_B_SCHEME=1` 的 remap writer 与 hook 协作
- `CGC_B_SCHEME=1` 走 B-scheme remap writer（llama-graph.cpp:2111-2118 的 host-leaf 或 GPU-table 变体）。需确认 hook 重写的 `ffn_moe_topk_remap` 与 B-scheme writer 不冲突（hook 应在 writer 之后、submit 之前改写，时序已由 DEFAULT 循环保证）。

### 🟡 R3：性能回归（可接受但需记录）
- 恢复循环后 SEG_BATCH 将付出 41 段串行开销（即它原本量的 ~79ms/step 中的 wait+cb+submit 部分）。这是正确性的必然代价，不影响交付锚点（交付锚点在不带 SEG_BATCH 的 prod-new async 路径，见 R4）。
- 记录：修复后 SEG_BATCH 臂的 step 耗时，确认落在 async 路径同级（而非单提交级）。

### 🟢 R4：不碰生产锚点（护拦）
- 修复**仅对带 `CGC_SEG_BATCH` 的臂生效**；prod-new async（交付锚点 11.61 t/s / MTP off / build 630）**完全不受影响**。
- 验收后跑 `mtp_off_baseline.json` v4 锚点，确认 decode 数字与 bit-identical 未漂移。

---

## 4. bit-identical 验证方案

主闸：`scripts/check/m123_oracle_gate.py --profile prefill250`
- 当前 `DIAGNOSTIC_KEYS` 含 `CGC_SEG_BATCH`/`CGC_SLOT_TABLE_GPU`/`CGC_RHO*`，**不含 `CGC_B_SCHEME`** → 跑时加 `--allow-incomparable`（或给 gate 加 `CGC_B_SCHEME` 到可比键）。
- 臂：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1`（修复后，不再需要 SEG_RECENTRE/S1_DBG）。
- 判据（D5）：**M1 ≥ 8/9**、M2 argmax 同、M3 top-N 同，对比基准 = async/prod-new 正确输出（同模型同输入同权重）。
- 结构判据（直接证明占位消失）：
  - `CGC-S1: EQUIV-PRESUB n_mismatch`：**113/层 → 0**（或该探针在正确路径下不再打印 mismatch）。
  - `CGC-G3-ZEROSLOT-TOTAL placeholder`：**250582 → 0**。
  - `id_oob` / `kIOGPUCommandBufferCallbackErrorOutOfMemory`：**消失**。
- 验证步骤：
  1. 应用 §2 diff → `cmake --build src/llama.cpp/build -j10`。
  2. 跑上述 gate，读 EXIT/M1/M2/M3。
  3. 同臂 stderr 抓 `EQUIV-PRESUB`（应 0 mismatch）、`ZEROSLOT-TOTAL`（应 placeholder=0）。
  4. 若 M1≥8/9 且结构判据归零 → 进 D5 提交闸；否则回到 R1 排查 GPU-table hook 早退。

---

## 5. 结论与决策

| 方案 | 可行性 | 结论 |
|---|---|---|
| (i) 提交后 patch | ❌ | fused-graph 错误 propagate，判死 |
| 方案 C 全专家常驻 | ❌ | 14.3GiB pool OOM（compute #41），16GB 盒子装不下 |
| **方案 B 逐层 ensure** | ✅ | **恢复 ggml-backend.cpp:1775-1792 被跳过的 41 段 wait→hook→submit 循环；复用 async 路径 proven 机制，~17 行删减，零新逻辑** |

**唯一剩余风险是 R1**（GPU-table 模式下 hook 内部早退）。若 R1 通过，方案 B 是 R6-DIRTY 的最小、最低风险、且有 async 路径背书的修复。

**诚实提示**：恢复循环后，SEG_BATCH 臂在机制上等同于 async 路径（失去「单提交」特性）。若 charter 字面要求「单提交路径本身正确」，那在 16GB 盒子上**架构上不可能**（全专家装不下 + 单提交无逐层 ensure 屏障）——R6-DIRTY 的可交付形态只能是「逐层 ensure 的正确路径」，而非「单提交正确」。

---

*附：a10 残留诊断码（a4 探针 + a9 relocate + a10 X feed）仍在 `src/llama.cpp/src/llama-context.cpp` 工作树未提交。方案 B 的修复在 ggml-backend.cpp（不同文件），与那些诊断 scaffold 正交；建议方案 B 验证通过后，把 llama-context.cpp 的诊断 scaffold 回退保持树干净。*
