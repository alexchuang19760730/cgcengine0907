# R6-DIRTY · 提交后校正 pass（方案 i）设计书

> 日期：2026-10-01 · 线 A · 配套 a10 负结果（见 `.workbuddy/memory/2026-10-01.md` §EN- a10 结果）
> 目标臂：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1`（M1 0/9，placeholder=250582，每层 113/256 专家被写成 `e%ns` 错 slot）
> 本文先给出 (i) 的**原本意图**（落点 / 接哪些 tensor / 如何验证 bit-identical），再用代码铁证说明它**为何结构性不可行**，最后给出可行的转向方案。

---

## 0. 现状（为什么会有 (i) 这个想法）

- SEG_BATCH 单段提交臂不触发 `expert_cache_on_topk` hook（ggml-backend.cpp:1773「No hook is fired」），于是 `cache_step_union` 恒空，P1 prev-token 预测从源头喂不进（a10 已证死）。
- 错位的写法在 `graph_compute` 的 pre-submit 写表块（llama-context.cpp:3902-3940）：对 `st[e]<0`（非驻留）的专家写 `v = e % ns`，即把专家 e 的 gather 指向「别人权重」的 slot。这是 placeholder 的来源。
- (i) 的直觉：图已经算完、输出已出，能不能在**提交后**把那 113 个错专家的 gather 结果换成正确权重算出的结果，从而不碰核心 fused-graph 逻辑？

---

## 1. (i) 原本意图：落点与要接的 tensor

### 1.1 数据链路（来自 llama-graph.cpp，已核实）
```
ffn_moe_topk          (selected_experts, :2105)   —— 每 token 的 top-k 路由专家 id  [n_expert_used, n_tokens]
   └─► ffn_moe_topk_remap leaf (:2111-2118, 主机可写 I32)  —— hook 把专家 id 改写成池 slot 索引
         └─► mm_id_ids = remap_ids (:2403/2412)  —— mul_mat_id 实际消费的索引
               └─► build_lora_mm_id(gate_up/up/gate, cur, mm_id_ids)  (:2578-2640)
                     └─► ffn_moe_gate_up / ffn_moe_up / ffn_moe_gate  —— 专家 FFN 输出 [n_ff, n_expert_used, n_tokens]
                           └─► 经激活/加权/sum → FFN 输出 → 加回 residual `cur`
```
- **要接的 4 类 tensor**：
  1. `ffn_moe_topk`（路由）：判定哪些 token 命中了 mismatched 专家 e。
  2. `ffn_moe_topk_remap` leaf（slot 映射）：诊断时比对 `td[e]` vs `st[e]`，定位 113 个错 slot。
  3. 专家 FFN 输出张量（mul_mat_id 产物）：被错权重污染的那部分。
  4. `st`/`td` 表（llama-context.cpp:3809/:3823）：真 slot 表 vs 发布表，区分 resident / 非驻留。

### 1.2 原设想的校正算法（伪码）
```
for each layer il:
  for each expert e where td[e] != (st[e]>=0 ? st[e] : -1):   # 即 113 个 mismatched
     ensure e resident  -> 取得正确 slot correct_slot
     for each (token t, k-slot) where ffn_moe_topk[t][k] == e:
        重算 expert e 的 FFN(t)，写入 FFN 输出张量对应位置
```
即：提交后、在 host 侧用正确权重重算那 113 个专家对命中 token 的贡献，覆盖回主图 FFN 输出 buffer。

---

## 2. (i) 为何结构性不可行（代码铁证）

### 2.1 单图一次提交，无分层屏障
`graph_compute`（:3574）把 41 层**全部 append 进同一张图 `gf`**，提交一次（`ggml_backend_sched_graph_compute`）。pre-submit 写表块（:3902-3940）的注释原话：
> *"BEFORE the submit so the gather inside this very graph sees this step's state."*

⇒ 整张图在一个 submit 内跑完，**层与层之间没有 host 可插入的屏障**。

### 2.2 FFN 输出加回 residual，错误沿 41 层 propagate
llama-graph.cpp:2578-2660 的 `mul_mat_id` 产物经 gate/up/激活后，按 transformer 结构**加回 `cur`（residual stream）**；`cur` 直接作为下一层的输入。于是：
- 层 L 的 FFN 若用了错权重 → `cur` 在层 L 末尾就错了 → 层 L+1…L+40 在**同一个 submit 内**已经用错值算完。
- 提交返回后，下游 40 层的计算早已固化在输出里。

### 2.3 每层都受影响，没有「干净起点」可 patch
a10 实测：`EQUIV-PRESUB` 在抽样的 il=11/14/31 等层**全部 113/256** mismatch（256−143=113，即每层都有 113 个非驻留专家）。错误从层 0（或第一个命中非驻留专家的 token）起就进入 residual，并贯穿全部 41 层。**不存在某层之后才需要校正的情况**——你无法「只 patch 最后几层」就还原正确输出。

### 2.4 根因是驻留性，不是计算
那 113 个专家**根本不在池里**（`st[e]<0`）。校正 pass 要拿到它们的正确权重，必须先 `ensure` 驻留——而加载权重进池是一个**需要同步屏障的异步操作**（host 从权重文件/内存搬进 GPU 池，GPU 的 mul_mat_id 必须等它完成）。单段提交恰恰**移除了这个屏障**（不再触发能触发 prefetch 的 hook），所以它在架构上无从加载按需专家。

> **结论**：(i)「提交后 patch 输出 buffer」对融合 41 层单提交图**不可行**——错误已向下游 propagate，且根因（驻留缺失）在单提交内无解。这是 a10 负结果之后、设计 (i) 之前必须承认的架构事实，不是实现细节 bug。

---

## 3. 真正可行的方向（(i) 收敛到 (ii) 类）

要让 SEG_BATCH 臂达到 M1 9/9，必须**不让错权重进入图**，二选一：

### 方案 A（推荐先评估）：提交前让本步所需专家全部驻留
- 等价于把 async 臂的「per-step ensure」搬到 SEG_BATCH 的 pre-submit 块：在 :3902 写表之前，用 `ffn_moe_topk`（本步路由，但 SEG_BATCH 下路由在图内才算得出来 → **这里又卡住**：路由是 post-routing，pre-submit 时还不知道）。
- 因此方案 A 需要「路由先算」——这又回到拆分提交或预测（P1，已死）。纯 pre-submit 无路由信息，**单独走不通**。

### 方案 B（真正可行）：拆分提交，每层之间插 ensure 屏障
- 把融合的 41 层图**拆成 41 次 per-layer 提交**（或按组）。每层提交前：
  1. 读该层 `ffn_moe_topk`（上一层算出的路由）或本层路由；
  2. `ensure` 该层 113 个非驻留专家驻留（加载权重，等屏障）；
  3. 用正确 slot 写 remap leaf（不再写 `e%ns`）；
  4. 提交该层。
- 这其实就是 charter 字面 P2「逐层重算」的**可行形态**，也等价于「放弃单段提交、改 routing-first→ensure→compute 的顺序」（原邮件里的方案 ii）。
- 代价：失去「一次提交」的吞吐红利；需重新设计 `graph_compute` 的提交循环与层间张量持久化。

### 方案 C（最粗暴但确定）：池扩容到 256/层
- 若池能容纳全部 256 专家/层，则 `st[e]>=0` 恒成立，placeholder 自然消失，`e%ns` 永不触发。
- 代价：显存/权重常驻量翻倍（当前 143/层 → 256/层），在 16GB 盒子上大概率装不下 41 层全专家——需先量峰值占用再判定。

---

## 4. bit-identical 验证方案（无论走 B 还是 C 都适用）

### 4.1 验收工具与判据
- 主闸：`scripts/check/m123_oracle_gate.py --profile prefill250`
  - 当前 `DIAGNOSTIC_KEYS` 含 `CGC_SEG_BATCH`/`CGC_SLOT_TABLE_GPU`/`CGC_RHO*`，**不含 `CGC_B_SCHEME`** → 需二选一：
    - 给 gate 加 `CGC_B_SCHEME` 到可比键（推荐，长期正确），或
    - 跑时加 `--allow-incomparable`（仅本次比对 logits）。
  - 判据（D5）：**M1 ≥ 8/9**（数值同）、**M2 argmax 同**、**M3 top-N 同**，对比基准 = async/prod-new 正确输出（同模型同输入同权重）。
- 结构判据：`CGC-S1: EQUIV-PRESUB n_mismatch` 必须从 **113/层 → 0**（错位消失）；`CGC-G3-ZEROSLOT-TOTAL placeholder` 从 **250582 → 0**。

### 4.2 护拦：不动生产锚点
- 修正必须**仅在该诊断臂生效**（门控 `CGC_SEG_BATCH && CGC_B_SCHEME`），**绝不触碰** prod-new async 路径与交付锚点 `11.61 t/s`（MTP off，build 630）。
- 验证后跑一遍 `mtp_off_baseline.json` v4 锚点，确认 decode 数字与 bit-identical 状态未漂移。

### 4.3 验证步骤
1. 实现候选（B 或 C）→ 构建 `cmake --build src/llama.cpp/build -j10`。
2. `m123_oracle_gate.py --profile prefill250 --env CGC_SEG_BATCH=1 --env CGC_B_SCHEME=1 --env CGC_SLOT_TABLE_GPU=1 [+fix flag] --allow-incomparable`，读 EXIT/M1/M2/M3。
3. 同臂 stderr 抓 `EQUIV-PRESUB`（应 0 mismatch）、`ZEROSLOT-TOTAL`（应 placeholder=0）、`id_oob`（应消失）。
4. 若 M1≥8/9 且结构判据归零 → 进 D5 提交闸；否则回到设计。

---

## 5. 决策请求

| 方案 | 可行性 | 风险 | 备注 |
|---|---|---|---|
| (i) 提交后 patch | ❌ 结构性不可行 | — | 错误 propagate + 驻留缺失，已判死 |
| B 拆分提交+层间 ensure | ✅ 可行 | 高（重写提交循环） | = charter P2 可行形态 / 原方案 ii |
| C 池扩容到 256/层 | ✅ 可行（若显存够） | 中（需量峰值） | 最省代码，但可能装不下 |

**建议**：先量方案 C 的显存峰值（若 16GB 盒子放得下 256/层全专家，则 C 是最小改动、最稳的 M1 9/9 路径）；放不下再走 B。两者都需另写实现设计书，**不应在现有 relocate 诊断 scaffold 上硬加**。

---

*附：a10 残留诊断码（a4 探针 + a9 relocate + a10 X feed）仍在 `src/llama.cpp/src/llama-context.cpp` 工作树，**未提交**。因 (i) 已证死，建议回退这批 scaffold 保持树干净，待方案 B/C 拍板后再开新实现。*
