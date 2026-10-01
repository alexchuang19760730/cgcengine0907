# 前提 B 重測：開跑前盤點（2026-09-28）

性質：**唯讀盤點 ＋ 視窗判定。零 GPU、零重建、零 `src/` 更動。**
目的：在花掉一個窗口之前，先把「該量什麼、量具是否活著、現在能不能量」三件事釘死。

---

## §1 先更正我自己（兩條，都會改動這次要跑的內容）

### 1.1 那條 one-line gate 修復**已經落地**，`premise_b` 的 (c) 過期了

`agent_harness/portal/targets.json` 的 `premise_b` 寫著：

> (c) THE FIX IS ONE LINE, PREPARED AND SYNTAX-VERIFIED, **NOT APPLIED**.
> `cgc_is_decode_graph(n_tokens, cgc_pool_max_tokens())` replaces `n_tokens == 1`.
> **It needs a build** …

**它已經在樹上了**：

```c
// llama-context.cpp:5526
if (cgc_churn_on && cgc_is_decode_graph(n_tokens, cgc_pool_max_tokens())) {
```

而 `:5501` 的註解就是那次修復的墓碑：

> `[CGC 2026-09-20 §G1-B] The gate used to be `n_tokens == 1`, which fires on NO delivery step.`

⇒ **不需要重建、也不需要套那個 patch。** 我在上一輪把「需要一次 build」當成前置，那是照著 `premise_b`
的舊狀態講的，不是現況。（`cgc_is_decode_graph` 是 `llama-cgc-phase.h:141` 的 inline，
判準是 `n_tokens <= decode_width`。）

### 1.2 前提 B 的 42.3% **不可能被 09-27 的 leaf 失效汙染** ⇒ §5.3 的理由不成立

`docs/S2_OVERLAP_EXPERIMENT_2026-09-28.md` §5.3 主張：

> 前提 B（2.3）要重測，理由是它的**來源 run 正是 2.9 那個失效**。

三個獨立理由說這條不成立：

| # | 理由 | 出處 |
|---|---|---|
| 1 | **42.3% 來自 `n_slot_table_consumed_by_ntok`**，一個 teardown 分桶計數器，**不是** `TABLE-CHURN graph= changed_entries` 那個 whole-table 量 | `premise_b` 「SO THE SPLIT WAS PUT IN THE INSTRUMENT」 |
| 2 | **計數點是 host 側的發布路徑**：`prev` = 該 (ctx, layer) 上一次發布的 table 快照；`now = (const int32_t *) table->data`；比較對象是 `ids[j]`（hook 自己發布的 top-k） | `llama-context.cpp:5655-5686` |
| 3 | **09-27 的修復對預設臂的執行期行為是零**：整個區塊在 `getenv("CGC_S1_DBG")` 之下，而它修的是**讀回用的區域指標**（`rm`/`tb` 被釘 `nullptr`），不是圖 | `docs/S1_LEAF_MEMBERSHIP_2026-09-27.md` §4 |

⇒ **「因為證據鏈死了所以要重讀 churn」是錯的。** churn 走的是另一條、一直都活著的量具。

---

## §2 但這次 run 仍然該做 —— 理由換成下面三條（其中第一條最硬）

1. **這個量具在 build 630 上從未印過一行。** 全 repo 220 個帶 `CGC_S1_TABLE_CHURN` 的產物**全部落在
   09-16／09-17**；09-27 之後的 log 裡唯一命中 `TABLE-CHURN` 的
   `Backup/cgc_logs/llama_server_latest.log` 是一條**斷掉的符號連結**
   （指向不存在的 `llama_server_20260928_202646.log`）。字串在 630 的 dylib 裡（`grep -c` = 2），
   但那只是**靜態**存在。⇒ 依 §8.4 的兩層證據規則，**它需要一次行為證據**。
2. **42.3% 是單一 run、而且不是這個 build。** `premise_b` 記的是 build `libllama=d91e432636d1`（09-20）。
3. **登記明文禁止把它當常數**：同一 signature 在 43 個舊 run 之間跨 **37.2%–84.4%**，
   所以答案是**分布**，一次讀數不是答案。

## §3 ★ 開跑前必須先裁決的一個矛盾：`ntok=4` 與 `MTP off` 不能同時成立

你指定的形狀是「delivery shape、ntok=4、MTP off」。**這三個不能同時成立**，而這是我上一輪的句子留下的。

- `n_tokens` 是 **top-k tensor 的 token 數**（`premise_b` (b) 自述）。
- **MTP on**：verify batch = k+1 個 token ⇒ 交付步就是 `ntok=4`（`{2:1, 3:2, 4:107, 8:18}`）。
- **MTP off**：decode 一步一個 token ⇒ `ntok=1`。
- 而 `premise_b` 自己量到的兩個桶是**不同 context**：
  `ntok=1 → 0/612 = 0.0%`（那是 **draft** 表，`key = il + 100000`，`is_draft`），
  `ntok=4 → 3449/8160 = 42.3%`（verify 表）。

⇒ 所以問題不是「跑哪個 cell」，是**問哪一個問題**。兩個選項**互斥**：

| | 讀數 | 口徑 | 有沒有被量過 |
|---|---|---|---|
| **A** | **`ntok=1` 主 context 的 churn**（MTP off、`prod-new` 權威 cell） | ✅ 今天認可的交付口徑 | **從未報過**（0/612 那個是 draft ctx，不是主 ctx） |
| **B** | **`ntok=4` verify 的 churn**（MTP on） | ⚠ 落在今天 `MTP_CALIBER_REDEFINE` 的「另一個輸出函數」之外 | 42.3%，但只有單一 run、舊 build |

**兩者的答案會指向相反的路**：A 若為 0 ⇒ 表在逐步之間是常數 ⇒ 發布冗餘 ⇒
**S3（40 段→1）復活**；B 已知 42.3% ⇒ 發布不冗餘 ⇒ S2 是唯一路。

## §4 視窗判定（`server_window.decision`，實呼）

```
reclaimable_mb = 6199.9 ~ 6206.5        need_mb = 6500  → admits = FALSE
refused_by = ["harness:memory"]（短 ~300 MiB）
其他全綠：foreign_llama=[]、port 8080 未被佔、compressor quiet (0.00 MiB/s)
launcher_admits = TRUE（free 75% >= req 40%）⇒ agree = FALSE，binding = harness
```

- 我自己的 state gate（`STATE_AVAILABLE_FLOOR_MB = 4000`，量 `pages_available_mb`）**會放行**，
  現在 `available ≈ 6210 MiB`。
- 擋住的是 `server_window` 的 `reclaimable ≥ 6500`（`reclaimable = free + purgeable + inactive`）。
- **300 MiB 的差距在擺動範圍內**（今天稍早一次壓縮風暴把它從 5171 推到 10134）⇒ 值得輪詢，不值得覆寫。
- 沒有引擎在跑（`ps` 精確比對 = 0）；`/tmp/flashkv_gpu_window.lock` 存在但**鎖是空的**，只是殘留檔。

## §5 一旦裁決，精確要做的事

```bash
cd ~/Documents/flashkv-devserver
# 前導（0 GPU，30 s）：量具活著 + 桶存在
#   讀 teardown 必須出現 per-width 那一行；沒出現要看是
#   「not instrumented」（旋鈕沒開）還是「沒有桶」（步沒進 gate）——兩者處置不同
python3 scripts/check/server_window.py            # 先確認窗口
harness bench --arm 'prod-new:CGC_S1_TABLE_CHURN=1;CGC_SLOT_TABLE_GPU=1;CGC_GPU_TIMING=1;CGC_DECODE_PROFILE=1' \
              --cell delivery --workdir /tmp/pb630      # 選項 A（on-caliber）
# 讀：llama_expert_cache: S1 slot-table: ... consumed_changed=…  + per-ntok rate 行
```

判讀紀律（沿用 `premise_b` (e)）：① 答案報**分布**不報常數；② `not instrumented` 與
「量到的 0」必須分得開；③ 開跑前後 `clamped_selected=0`、`zero_mapped_selected=0` 要一起看。

## §6 本檔不主張

- 不主張 42.3% 錯。它**沒有被汙染**，但它**舊且單點**。
- 不主張 S2 或 S3 誰對。§3 的兩個讀數都會改動那個判斷，而**都還沒量**。
- 不主張這次 run 會給出 25。前提 B 只決定**走哪條路**（S2 上限 +9%，見 `exp-s2-overlap`）。

## §7 ★ 追補（同日，零 GPU）：`ntok=1` 的 0.0% 已經在樹上了 —— 但它是 **draft** 的

§3 把「`ntok=1` 主 context」列為本次要量的目標。追查後它在**樹上已經有一半**，而那一半正好是會
騙人的那一半。本節是這次 run 為什麼仍然必須做的完整理由。

### 7.1 找法：我先前搜的是識別字，不是**輸出字串**

`consumed_by_ntok` 是 C++ 變數名，log 裡永遠不會出現 ⇒ 先前兩輪「全站 220 個產物只有 09-16/17」
的結論量錯了對象。真正的輸出是 `llama_expert_cache: S1 churn by ntok (consumed subset):`
（`llama-expert-cache.cpp:3098-3112`）。用字串重搜：

```
Backup/cgc_logs/llama_server_20260920_030832.log   03:11
Backup/cgc_logs/llama_server_20260920_121225.log   12:13
Backup/cgc_logs/llama_server_20260920_130514.log   13:07
```

**只有這三份，全部 09-20。** 09-27 那份（最新）沒開 `CGC_S1_TABLE_CHURN`，而且它還被
`Received SIGTERM` 擋掉（reader 判 REFUSE）⇒ 樹上**沒有一份含 09-27 修復的可讀樣本**。

### 7.2 那三份 log 的 per-ntok 讀數（讀得出來的部分）

```
030832   ntok=1   0.0% (0/612)     ntok=4  42.3% (3449/8160)   ntok=8  89.0% (3701/4159)
121225   ntok=1   0.0% (0/306)     ntok=4  42.4% (1729/4080)   ntok=8  89.6% (1863/2079)
130514   同 121225（獨立 session、同 workload，逐項重現 ⇒ 計數器是確定性的，不是抄的）
```

### 7.3 ★ 為什麼 `ntok=1` 那一格是 **draft**，不是交付步

三條獨立證據，其中第一條是原始碼自己寫的：

1. **原始碼註解**（`llama-expert-cache.cpp:3028-3031`）：
   `verify = ctx_tgt multi-token, draft = ctx_dft 1-token`。
2. **兩個計數器在同一批 run 上等值**：`MTP fast path: … draft: calls=` 在 030832 是 **612**、
   在 121225 是 **306**，而 `ntok=1` 的分母正好是 **612 / 306**。`n_fast_draft_calls` 在
   `llama_expert_cache_touch()`（`:383`）每次呼叫加一，粒度是 (step, layer)。
3. **一個 cache、兩個 context**：`612 + 8160 + 4159 = 12931 ≈ publishes 12971`。

⇒ 在那兩次 run 裡，**主 context 貢獻的 `ntok=1` publish 是 0**（MTP on：主 context 只走 ntok=4 的
verify 與 ntok=8 的 prefill）。§3 的矛盾因此有解：`0/612` 與 `42.3%` **確實屬於不同 context**，
而交付步那一格**從未被量過**。

### 7.4 讀數器本身有兩個 bug，都已修（`scripts/check/premise_b_read.py`）

| # | bug | 後果 | 修法 |
|---|---|---|---|
| 1 | `ntok<=4` 一律標成「decode/MTP-verify step」 | 把 draft 的 `0.0%` 讀成「交付步無 churn ⇒ S3 免費」——**正是本節要防的那個誤讀** | 讀 `draft: calls` 做歸屬；新增 `delivery_ntok1_measured` 這個布林，只有 MTP off 才為 True |
| 2 | `BYNTOK` 以 `(.*)$` 收尾（無 `re.M`、`.` 不跨行） | **只有當該行恰好是檔案最後一行才配得上**——09-20 那三份剛好如此，所以沒被發現 | 改 `([^\n]*)` |

selftest **24/24 PASS**（新增 7 例，含 MTP-on=draft、MTP-off=main、桶比 draft 大 ⇒ mixed）。
兩個 bug 都不是「程式碼錯了」而是「**註解說得比量到的大**」——與本 session 前四次抓到的同一族病。

⚠ 還有一個**未修**的量具不對稱，供後人查核：`_by_ntok` 計數器**沒有** `is_draft` 守衛
（`llama-context.cpp:5678-5687` 無條件累加），而同一函式的 per-graph `SEL-DRIFT` 迴圈**有**
（`:5610` `if (kv.first >= 100000) continue;`）。⇒ **同一份 run 的兩個數字，一個含 draft、一個不含。**
本節不改 `src/`（改它要重建，且會動到既有判詞的口徑）；但**跑 MTP-off 臂時這個不對稱會自動消失**
（沒有 draft context ⇒ `_by_ntok` 只有主 context）。

### 7.5 這對 ① 的機率意味著什麼（要誠實，兩個方向都有）

**往上**：同一份 run、同一個 pool、同一個 cache，只有步寬不同 ⇒ **1 token 0.0%、4 token 42.3%、
8 token 89.0%**。這是**同 run 內對照**，不是跨 run 比較，「churn 隨步寬上升」這件事因此從假設
變成量到的事實，而 ① 正是靠這個機制。

**往下**：draft 是 **ctx_dft**，路由與池壓力都跟主 context 不同（它自己在同一個池裡被主 context 的
verify 步擠），所以那一格**不能**當成「MTP-off 主 context 會是 0」。門檻沒有因此被滿足，只是先驗
被推高。**能結案的仍然只有一次 MTP-off + `CGC_S1_TABLE_CHURN=1` 的 run。**

⇒ 本次 run 的判準不變（`exp-churn-delivery-630`），但 falsification 分支現在有機器判準：產物必須
讓 `premise_b_read.delivery_ntok1_measured == True`，否則就是「**沒有讀數**」，不是「churn=0」。
