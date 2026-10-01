# operator 裁定：L25-4（MTP 軸）走 MTP-on 獨立口徑（2026-10-01）

## 裁定

- operator 2026-10-01（原話）：「**MTP on 沒問題**」。
- ⇒ **25 以 MTP-on 為獨立口徑推進**（D0 = yes）。D0 已先在 `docs/CRITICAL_PATH_2026-09-30.md` v2 記下；
  本檔是 L25-4 的**決策記錄**（卡片 `accept` 要的那一份）。
- 兩條不變的規矩：① MTP-on 與 MTP-off **不可互比**（輸出函數不同；測試卡檔頭標籤、門 2）；
  ② 不准寫「ON＝OFF 的 X%」。

## 這一拍的效力（L25-4）

- 前置①（產品／口徑決策）**解除**：MTP-on 讀數可作 25 的候選口徑。
- 前置②（機制）**沒有解除** —— 卡點現在只剩 **S1**：`is_mem_shared` 的 draft 輪級掉線
  （61% 輪 `draft=0`；verify init 失敗 144/194 是 `X>Y`）⇒ 把 `k_eff` 從 1.59 拉回 k。
  達標句（跑前寫死）：**k=8 時 `k_eff` ≥ 7** 且 position error 密度 ≤ 乾淨批的 32/11075
  ⇒ 帶入 a ≥ 0.484 ⇒ **≈25.2 t/s**。
- 盒子前提不放寬：MTP-on 存活線 ≥7703 MB；`budget_gate` 對交付 cell 現判 OVERBUDGET
  ⇒ S1 第一趟先帶足跡閘；若連它都跑不完 ⇒ **盒子判死**（不是機制判死）。
- 收貨（卡片 `accept` 不變）：**決策記錄（本檔）＋ 一次合規的 on/off 成對**。

## 下一步（S1）

- 樹上已有一支**未提交**的 attribution 臂 `CGC_MTP_NO_CTX_OTHER=1`
  （`src/llama.cpp/src/llama-context.cpp`；非 revert、預設位元不動；已進 `libllama.0.0.630.dylib`）
  —— 它把 draft 放回「不共用 KV」的 regime（09-24 乾淨批的那個）。
- 2026-10-01 本線把它的**轉送**補進 `scripts/run_server.sh`（`SERVER_ENV` 白名單）——
  bench 的 env 由那支腳本當唯一來源，不轉送就是空轉一趟。
- 量測臂 caps 放大（k=7 需 `min_usable ≥64`/層；09-30 的 `40-40:140` 已驗到 width=8）；
  產物過 **M1/M2/M3 oracle（以 ON 為獨立口徑）**後才算資料。

## S1 第一趟：發車前預註冊（2026-10-01 15:45）

**臂**（交付 cell、`p0_n128_d512_r3`、`--spec-type draft-mtp --spec-draft-n-max 7`）：

- **A（對照）**：`CGC_SERVER_MTP=1;CGC_SERVER_LAYER_CAPS=40-40:140;CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1;CGC_MTP_PERF=1;LLAMA_BENCH_SPEC_DBG=1`
  —— 09-30 15:31 width=8 那趟的完整複製。
- **B（S1 處理）**：同 A ＋ `CGC_MTP_NO_CTX_OTHER=1`（`src/llama.cpp/src/llama-context.cpp:188`，engine 側已存在、
  已進 `libllama.0.0.630.dylib`；把 draft 放回「不共用 KV」— 09-24 乾淨批的 regime）。

**命令**（`--dry-run` 已驗：cell 口徑 15/15、兩臂 `arm env` 逐項正確）：

```bash
cd ~/Documents/flashkv-devserver && python3 scripts/check/llama_bench_matrix.py \
  --arms "prod-new:CGC_SERVER_MTP=1;CGC_SERVER_LAYER_CAPS=40-40:140;CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1;CGC_MTP_PERF=1;LLAMA_BENCH_SPEC_DBG=1,\
prod-new:CGC_SERVER_MTP=1;CGC_SERVER_LAYER_CAPS=40-40:140;CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1;CGC_MTP_PERF=1;LLAMA_BENCH_SPEC_DBG=1;CGC_MTP_NO_CTX_OTHER=1" \
  --prompt 0 --gen 128 --depths 512 --reps 3 --warm-skip 64 --batch 512 --ctx-size 4096 \
  --cell delivery --spec-type draft-mtp --spec-draft-n-max 7 \
  --arm-timeout 420 --stall-watch 90 \
  --workdir Backup/l254_s1_20261001/k7 --json Backup/l254_s1_20261001/k7_paired.json
```

（`llama_bench_matrix.py` 是內部驅動而非正式入口；本趟是**歸因趟** —— B 臂按引擎自己的註記「never a throughput
figure」，產物只讀機制量、**不進 k 曲線、不當可引用 t/s**。）

**端點讀法**：`CGC-MTP-PERF` 最後一行的 `gen_tok_per_round`（＝`k_eff`）與 `acc_rate`；`LLAMA_BENCH_SPEC_DBG`
的 `draft=` 直方圖；pos-err 密度＝該臂引擎 stderr 的 M-RoPE 拒收行數 ÷ 該臂總行數（對比乾淨批 `32/11075`）。

**窗口合格條件**：A 臂必須重現 09-30 指紋（`k_eff` ≈1.6、`draft=0` ≈60%、`MTP fast path` draft:verify ≈1:52）——
不重現 ⇒ 本趟窗口不合格，B 臂讀值不採用。

**達標／否證**：B 的 `k_eff` **≥ 7**（k=7 是 cap=8 下最寬的可達 verify；09-30 已量到 width=8）且 pos-err 回到
~0（09-24 乾淨批等級）⇒ S1 假設成立 ⇒ 續做 k=8（需 caps 再放大或 verify 佈局改對）。
**kill line（事先登記）**：B 的 `k_eff` ≤ 2、或只比 A 高 <0.5 ⇒ **`is_mem_shared` 單獨不是卡點** 
⇒ 不再重跑同一 regime，改走「把共用 KV 的 verify 佈局做對」或依卡 falsify（M 軸退出 25）。

**足跡閘（跑前必過）**：`python3 scripts/check/l255_close.py --check` 需 `VERDICT: OK`（存活線 ≥7703 MB）。
2026-10-01 15:45 現況＝**SHORT**（可回收 5170 MB／短 2533 MB，其餘四項綠）⇒ **未發車**。

## 出處

- operator 原話：本線對話 2026-10-01（「MTP on 沒問題」）。
- D0 前記：`docs/CRITICAL_PATH_2026-09-30.md` v2（「算子决策已就位：D0 = yes」）／v3.1 尾巴。
- 卡點事實：`scripts/check/decode_board_2026-09-29.yaml` L25-4 的 `state`
  （`is_mem_shared`／61% `draft=0`／width=8 那趟 `k_eff` 1.590）。
