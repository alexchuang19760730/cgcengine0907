# S2-c 补丁方案（ρ capture 搬出 per-layer hook）— 2026-09-30 18:4x

⚠ 本档是**设计＋预注册**，不是已落地的改动：截至写档时 `src/llama.cpp/src/llama-context.cpp`、
`src/llama.cpp/ggml/src/ggml-metal/**` 仍在他线的工作树里（117 档未提交）⇒ 动工要等它释放，
且建置会嵌入他线的未提交源码（provenance 必须注明）。本档未编译、未验证。

## 1. 为什么需要它（今天实测坐实）

- 单段提交臂（`CGC_SEG_BATCH=1`）**跳过** per-layer hook（`llama-context.cpp:4103` 注释：
  "SKIPS THE HOOK"）。
- ρ 的影子 logits 由 `cgc_rho_capture()`（:4664）写入 `g_rho_logits[ul]`，而它**只在**
  `llama_context::expert_cache_eval_cb`（:4798，:4837 处调用）里被触发。
- `cgc_rho_prefetch()`（:4708）开头就检查 `g_rho_logits[ul].empty()`（:4727）⇒ 空则 return。
- ⇒ 单段臂上 `g_rho_logits` 恒空 ⇒ **ρ prefetch 是结构性 no-op**（S2-b 实测：池计数器三趟逐项相同）。
- 对照：交付入口（`CGC_RHO_FILL`）实测 `per_layer=8.00 cum_skipped=0` ⇒ 机制在有 hook 的臂上是活的。

注意一个关键区分：**影子节点本身是建出来的**（`qwen35moe.cpp:224` 在 `CGC_RHO_PROBE` 下建
`cgc_rho_logits-L` 并 `ggml_build_forward_expand`），被跳过的只是**读回它的那条通路**。
⇒ 补丁要补的是"读回"，不是"建节点"，也不是"恢复整个 hook"。

## 2. 补丁（两处触点，比照 P1 的形状）

P1 已经示范过同样的动作：把 feeder 从 `_DBG` 里拆出来，用单段路径**已有的读回通道**
（`llama-context.cpp:4129`：`CGC_SEG_BATCH && (cgc_miss_mask_dbg || cgc_rho_feed)`）承载。
S2-c 照抄这个形状：

- **触点 A（`qwen35moe.cpp`）**：建影子节点时把张量指针存下来（例如 `ctx` 上的 `g_rho_node[il]`，
  或既有的 `cb(rho_logits, "cgc_rho_logits", il)` 旁边加一个登记）。
  ⇒ 让"读回"能在没有 hook 的位置找到它。
- **触点 B（`llama-context.cpp:4129` 那一段）**：在既有的单段读回分支里，
  于**同一个 synchronize 之后**（不新增同步）对 `g_rho_node[il]` 调 `cgc_rho_capture()`，
  再照原样 `cgc_rho_prefetch(il)`。gate 形状同 P1：`CGC_SEG_BATCH && (…新增开关…)`。

为什么这样放：:4129 那里**已经**在 synchronize 之后读 `ibuf`（该步的 routed ids）⇒ 影子节点的
数据此时同样是"已经算完"的，多读一个张量不引入新的等待点 —— 这正是 P1 那条「feed-only path
never touches the mask」的同一原则。

## 3. 风险与诚实边界

- 影子节点在单段图上是否真的被计算（`ggml_build_forward_expand` 之后是否在执行的子图里）
  **未验证** ⇒ 补丁的第一件事是印一行见证（如 `CGC-RHO-CAP`），确认读回非空。
- 多读一个张量＝多一次 device→host ⇒ **这就是 4.76 漏掉的那笔 host read**，量它时要用
  计数器＋仪器端点（见 S3-b 的做法），不要用墙钟。
- 若补丁后 M1 仍 ≤ 2/9 ⇒ 结论是"分歧在单段提交图本身（slot/mask 映射，`CGC_B_SCHEME` garbage）"
  ⇒ 那是**另一种**引擎手术，规模与风险要重新评估，本档不再主张继续。

## 4. 预注册（跑前写死）

- 节点：**L20-1**（机制侧触及 L20-7 的 ρ）。
- 臂：`prod-new:CGC_SEG_BATCH=1;CGC_MISS_MASK=1;CGC_RB_FEED=1;CGC_RHO_PROBE=1;CGC_RHO_FILL=1`
  （＋新开关），对照＝参考 `ref_..._v9_20260930.jsonl`（已记录 `probe_prompt_md5`）。
- 端点：**M1 numeric identity**（`logits_fnv1a64` 全等）n≥9、M2 argmax 一致；辅助计数器＝
  `CGC-RHO-CAP` 层数、`CGC-RHO-FILL` per_layer。
- 判別句：**M1 ≥ 8/9 ⇒ R6 解除 ⇒ 20+ 臂可引用**；≤ 2/9 ⇒ 分歧在图本身 ⇒ 转真引擎手术。
- 前置：① src 释放并落地本补丁 ② 建置（会嵌入他线源码 ⇒ 产物注明 provenance）
  ③ server 窗口空（建置前跑会 abort 的闸门：listener ＋ `pgrep`）。

## 5. 与 S3-c 的顺序建议

S3-c（干净窗重测 ρ 价格）**不挡** S2-c，但它推的是支线（ρ 只对诚实臂有效，而诚实臂是 10.7 t/s
那一侧）⇒ 建议转 opportunistic：有干净窗顺手跑，不专门为它等。
EOF
echo written