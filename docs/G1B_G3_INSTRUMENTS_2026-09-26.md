# G1b / G3 儀器：生效證明與讀數（2026-09-26）

**一句話**：兩個閘門的「武裝」前置都已補完並實證；兩支儀器都已實跑讀出 ——
**G3 生效**（非駐留專家改為貢獻 0，`e % ns` 在武裝時**完全不被走到**）；
**G1b 量到 `0.395 ms/step`，超其 `0.2 ms` 預算的 1.97 倍，而 99% 的成本在 78 次跨後端小讀、不在排空**。

---

## 1. 臂、cell 與 build 指紋

```
arm = prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;
      CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1;CGC_ZERO_SLOT=1
cell = p2048 n128 d512 r3（harness.py bench 標準 cell）
rc = 0（存活）
build = libllama.0.0.578.dylib aa25a8782f86f994
產物 = Backup/phase_decomp/g1b_g3_smoke2/（權威）／g1b_g3_smoke/（舊印）
```

⚠ **`CGC_SEG_BATCH=1` ⇒ 這是單段臂**（hook 不跑 ⇒ 無 fill）⇒ 本文件**不引用任何 t/s**。

---

## 2. G3：生效，且可證

```
CGC-G3-ZEROSLOT: il=9 ns=143 zero_slot=142 (reserved slot exists and is now zeroed)
CGC-G3-ZEROSLOT-TOTAL: zero_slot=3398260 placeholder=0
```

- `zero_slot = ns - 1 = 142`（**不是 `-1`**）⇒ 保留槽**真的存在且已被清零**。
- **`placeholder = 0` 跨 3,398,260 次 cell 寫入** ⇒ **武裝時 `e % ns` 這條路完全不被走到**，
  非駐留專家一律指向已清零的保留槽 ⇒ 貢獻恰為 0。

**為什麼這是「接線」不是「實作」**：機制本來就在池裡（`llama-expert-cache.{h,cpp}`：
`zero_slot()` = `slots_l-1`、`usable_slots()` = `ns-1`、`zero_reserved_slot()` idempotent，
且 10+ 個領取點都遵守 `usable_slots`）。缺的是**可達性**，而且有兩個獨立原因：

| # | 原因 | 處理 |
|---|---|---|
| (a) | `zero_slot_enabled()` 只認 `CGC_VERIFY_DECODE`／`CGC_DRAFT_DECODE`，`prod-new` 兩者皆無 | 新增**專屬**鍵 `CGC_ZERO_SLOT`；加進 `run_server.sh` allowlist（在 `SERVER_MTP` block **之外**） |
| (b) | `zero_reserved_slot` 只被 **MTP fast path** 呼叫（`llama-context.cpp` `if ((verify_fast \|\| draft_fast) && cgc_fast_eligible)`） | 在 B-scheme 路徑**自己呼叫**它（idempotent） |

⛔ **不用 `CGC_VERIFY_DECODE` 當開關**：它同時在 `llama-context.cpp` 開 `verify_fast`、
改變走哪條 `ensure_batch` 路徑 ⇒ 兩個效果糾纏、無法歸因。

---

## 3. G1b：量到，且超預算

390 步全部有印，`nsel0 = 8`（＝ `n_expert_used(8) × n_tokens(1)`）⇒ **純 decode、ntok=1**。

| 量 | 值 | 佔比 |
|---|---:|---:|
| `total_usec`（整段 mask 回讀，含排空） | **394.6 µs/step**（steady 422–513） | 100% |
| `sync_usec`（`ggml_backend_sched_synchronize`） | **3.8 µs** | 1.0% |
| `read_usec`（＝ total − sync；78 次 `ggml_backend_tensor_get`） | **390.8 µs** | **99.0%** |

```
CGC-MISSMASK-COST: step=390 total_usec=513 sync_usec=5 ngets=78 nsel0=8 | \
  avg_usec total=394.6 sync=3.8 read=390.8 (ngets/step=77.0) steps=390
```

- **判準是 ≤ `0.2 ms/step` ⇒ 實測 `0.395 ms` ⇒ 超 1.97 倍（FAIL）。**
- 成本 ＝ **78 次跨後端小讀**（39 層 × 2），每次約 **5.0 µs，而每次只搬 32 bytes**
  ⇒ **是呼叫開銷，不是頻寬**。
- ⇒ 若要付更少：把 39 層 × 2 的 mask/ids **併成 2 個大張量**（78 次 → 2 次）。
  ⚠ **但該項只佔 step 的 0.25%（0.4 / 157.8）** ⇒ 即使歸零也只值 0.25%，**不該優先做**。

**計數器完好性判據**（新增欄位不能改壞既有格式）：

```
MISSMASK = 15004   CGC-MISSMASK-STEP = 390   CGC-MISSMASK-COST = 390    ← 三者 1:1
```

---

## 4. 這一輪順手修掉的兩個真缺陷

1. **建置失敗被埋住** ⇒ `llama-context.cpp: error: use of undeclared identifier 'n_tokens'`。
   `llama_context::graph_compute(ggml_cgraph*, bool batched)` **沒有 `n_tokens`／`ubatch`**。
   改用迴圈內已算出的 `ntot`（＝ `n_expert_used × n_tokens`）當 `nsel0`。
   ⚠ **若不 grep build 的 `error`，dylib 會保持舊的而 mtime 不變** ⇒ 那支臂「看起來在跑、其實沒帶儀器」。
2. **我自己儀器的標籤謊**：第一版把 `gets`（**次數** 77–78）印在 `avg_usec … gets=…` 的位置
   ⇒ 讀者會以為「讀取要 77 µs」。已改為 `ngets=`（計數）＋ 新增 `read=%.1f`（時間），並在註解寫明量綱。

**allowlist 側也修掉一個我自己造成的重複**：`CGC_RHO_PREFETCH_MAXQ` **自 2026-09-23 就已轉發**
（`run_server.sh` 的 `rho fuse` 區塊），我先前「補 allowlist」時把它寫了第二份 ⇒ 已移除重複。
⇒ **教訓：新增 allowlist 鍵之前先 `grep -c "SERVER_ENV+=(<KEY>="` 掃全檔**（該檔 2500 行、100+ 個 if-block，
沒有任何一處是「全部」）。

---

## 5. 明確不宣稱的（誠實邊界）

- ⛔ **本文件的 t/s 一律不可引用**（單段臂、hook 不跑 ⇒ 輸出 garbage；`pp 342.09 / tg 26.22`
  ⇒ 依本專案規則 **>20 t/s 的 S1 讀數一律不引用**）。attribution 亦為 `both`（thermal HEAVY）。
- ⛔ **G3 不會讓 G1 通過**：它把「用了別的專家的權重」換成「該專家貢獻 **0**」——
  **有界、確定、可解釋，但不等於分段臂的結果**。G1 的通路仍是 G4（per-expert 重算）或 hook；
  **G3 是它的前置，不是它的解**。
- ⛔ **G1b 的 0.395 ms 是「單段臂」上的讀數**；分段臂上 mask 回讀同樣會跑，但**未量**。

---

## 6. 前置與下一步

- **兩個武裝前置都已補完**（`run_server.sh` 四鍵：`CGC_RHO_PROBE`／`CGC_RHO_FILL`／
  `CGC_MISS_MASK_COST`／`CGC_ZERO_SLOT`；`llama-expert-cache.cpp` 的 `CGC_ZERO_SLOT` clause）。
  驗證：未設時解析後環境 **0 個新鍵**（惰性）；武裝時 **4 鍵全到**（穿透）。
- **現行 build**：`aa25a8782f86f994`。⚠ **跨 build 的 A/B 無效** —— 今日指紋序列
  `33ae1ad9b96a62da`（G2b）→ `3a3b9f62f82f2070` → `aa25a8782f86f994`。
- **未提交**：`llama-context.cpp`（ρ 閘門 ＋ G1b 計時器 ＋ G3）、`llama-expert-cache.cpp`、
  `scripts/run_server.sh` ＋ 隨源碼重建的 dylib。
- **下一步候選**（依 G1b/G3 的結果）：① 真正的 G1b/G3 量測臂（分段臂上量 G1b 的 0.395 ms 是否同量級）；
  ② 依 run sheet 的順序推進（`G3 → G1/G2 → G1b → G2b → G5`）。
