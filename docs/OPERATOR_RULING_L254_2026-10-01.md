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

## S1 第一趟：結果（2026-10-01 16:05，兩趟重現）

發車：`harness bench --charter scripts/check/charters/e-l254-s1-kv-2026-10-01.yaml`（兩臂同場、交付 cell、
`--spec-type draft-mtp --spec-draft-n-max 7`、caps `40-40:140` ⇒ 兩趟都印 `cap=8 routable=139 slots -> width=8`、
`LAYER_CAPS total 5860（min 140/層）`）。產物：`Backup/l254_s1_20261001/{k7_paired.json,k7_paired.stderr.log,k7_paired_clean.json,k7_paired_clean.stderr.log}`。

| 端點 | **A（`shared_kv=1` 對照）** | **B（`shared_kv=0`，＋`CGC_MTP_NO_CTX_OTHER`）** | 預註冊門檻 |
|---|---|---|---|
| `k_eff`（`gen_tok_per_round`） | 1.75–1.97（兩趟各 6 行） | **7.000**（每一行、兩趟） | ≥7 ✅ |
| `draft=0` 輪次 | 48% / 48%（172/356、143/300） | **0% / 0%**（0/113、0/94） | ≤10% ✅ |
| 每輪 draft 加權平均 | 2.52 / 2.64 | 4.87 / 5.10 | — |
| `draft=7` 輪次 | 98/356、89/300 | 65/113、57/94 | — |
| M-RoPE 拒收行 | 180 / 153 | **2 / 2** | 回 ~0 ✅ |
| 引擎 `draft-init` 失敗 | 6 / 6 | **0 / 0** | — |
| `MTP fast path` draft:verify | 1:41 / 1:32 | **1:12.6 / 1:11.9** | — |
| `acc_rate` | 0.34–0.50 | **0.52–0.61** | — |

⇒ **機制端點成立：`is_mem_shared` 就是 `k_eff` 卡在 1.6 的原因。** 關掉 `ctx_other` 後 draft 回到自帶 KV：
`k_eff = k = 7.000` 逐輪、`draft=0` 消失、M-RoPE 拒收 180→2、`acc_rate` 反升（0.43-0.50 → 0.52-0.61）。
09-30「width 只解跑不跑得完、不解 k 有多深」的結論被證實並被解掉。

⇒ **但速度一律 VOID**：兩趟四臂的 `attribution` 都是 **thermal（worst HEAVY）**，第二趟 B 更是 HEAVY 起跑
（A hist＝NOMINAL 79/MODERATE 22/HEAVY 5、B hist＝HEAVY 119）。依本檔預註冊的 `on_fail` ⇒ **t/s 判讀 REFUSE**。
機制讀數是計數器（tokens/round 是結構量，不受節流影響），所以機制結論不受此限。

⇒ **附帶發現（gated，需涼窗口才可讀）**：B 的每輪成本 72 ms（emit 4.0 tok）vs A 15 ms（emit 1.77 tok）
⇒ 每 emit token 8.5 → **18.2 ms（2.1×）**；t/s 10.65 → 9.64 但 B 起跑已 HEAVY。`gen_tok_per_round` 追平 k 之後
**每輪代價也追了上去** ⇒ 問題從「量不到」變成「量得到但不划算」，這正是 k 軸經濟學要判的那個問題。

⇒ **收貨狀態**：S1 的**機制端點**（`k_eff ≥ 7`、pos-err 回 ~0、`draft=0` 0%）**已達標且兩趟重現**；
**k=8 續不續做**、以及「k_eff=k 之後 t/s 沒變好」要不要走卡上 falsify，留待冷窗口的一趟（B 先跑）再拍。

⇒ **工具發現（不影響本結論）**：per-arm live log 檔名被 200 字元上限截斷 ⇒ 兩臂寫到**同一個檔名**
（本趟兩臂的 `live log :` 印的是同一條路徑）；能被追認的是成對 log `k7_paired*.stderr.log`（可用
`shared_kv=` 分段）。另：交付 cell 的 `fixed_fill_seed: null` 經 harness 調成 1（列為「可運行調整」），嚴格維度 14 項一致。

## S1 第二趟：k 軸經濟學（發車前預註冊，2026-10-01 16:26）

**問題**：`k_eff` 已經 = k = 7 了，decode 有變快嗎？（第一趟的線索：每 emit token 8.5 → 18.2 ms，但四臂 thermal ⇒ t/s VOID）

**端點**：

- **主**：decode **t/s**（交付 cell、`p0_n128_d512_r3`、成對、兩臂 `attribution=none`、全 rep max/min ≤1.10）。
- **副**：**每 emit token 成本** = `ms_per_round ÷ emit_tok_per_round`（`CGC-MTP-PERF` 最後一行；純計數器，不受節流影響）。

**判準（跑前寫死）**：

- `B_tps ≥ A_tps`（同窗、乾淨）⇒ **k 軸有增益** ⇒ 續往 k 軸投入（25 的天花板按 `E = 1 + a·k_eff` 重算）。
- `B_tps < A_tps` **但**每 emit token 成本 `B < A` ⇒ **半開**：k 軸本身有效，但當前實作（verify 寬度 8／池席位）把它吃掉了 ⇒ 指名吃它的那一項。
- `B_tps < A_tps` **且**成本 `B ≥ A` ⇒ **DEAD**：`k_eff=k` 不划算 ⇒ 25 在 MTP 軸判死（附機制），L25-4 收格。

**發車紀律**：**B 先跑**（第一趟的教訓：先跑的那臂拿 NOMINAL，後跑的那臂起跑即 HEAVY），兩臂各自等 NOMINAL（harness 的 `--cool-max-s 420`；若後跑那臂仍 HEAVY ⇒ 冷卻後重跑該臂）。

**產物**：`Backup/l254_s1_20261001/k7_econ{B,A}.json`（＋對應 stderr）；讀值回填本節。

## S1 第二趟：結果（2026-10-01 16:3x）—— DEAD

發車：`harness bench --charter scripts/check/charters/e-l254-s1-kv-2026-10-01.yaml`，**B 先跑**（照上節的紀律；各臂 `--cool-max-s 420`），
兩臂**只差** `CGC_MTP_NO_CTX_OTHER`。產物：`Backup/l254_s1_20261001/{k7_econA,k7_econB,k7_econB2}.json`。

| 端點 | A（`shared_kv=1`） | B（`shared_kv=0`） | 性質 |
|---|---|---|---|
| `k_eff` | 1.789 | **7.000** | 計數器 ✅ |
| `emit_tok_per_round` | 1.744 | **3.949**（2.26×） | 計數器 ✅ |
| `ms_per_round`（draft 相） | 16.07 | **55.22**（3.44×） | draft 相計時 |
| **draft 成本 / emit token** | **9.22 ms** | **13.98 ms（+52%）** | ← 副端點 |
| `acc_rate` | 0.416–0.444 | 0.416–0.444 | 計數器 ✅ |
| wall **t/s** | 8.60 | 7.48（−13%） | ⛔ **VOID**（`all_spread` 1.19 > 1.10；第三趟 B2 更直接 `attribution=swap`） |

⇒ **判詞：`DEAD`**（主端點 t/s 因窗口不乾淨判 REFUSE，但預註冊的副端點——「`k_eff` 追平 k 之後每 emit token 不變便宜」——
已量實：+52%，且 emit 只多 2.26× 而 draft 相時間多 3.44× ⇒ **邊際超線性**）。⇒ **25 在 MTP 軸判死**、L25-4 收格。
判決文與 reopen 條件：`docs/L254_K_AXIS_ECON_2026-10-01.md`。

**沒否證的**：S1 的機制修法本身（`k_eff` 1.8 → **7.000** 是真的有效）；要交付 MTP-on 時那個 KV 佈局仍是必要條件。
**這一趟也確認**：`E = 1 + a·k_eff` 的「每輪成本固定」前提在本實作／本 caps 下不成立（25.20 的預測失效）。

## 出處

- operator 原話：本線對話 2026-10-01（「MTP on 沒問題」）。
- D0 前記：`docs/CRITICAL_PATH_2026-09-30.md` v2（「算子决策已就位：D0 = yes」）／v3.1 尾巴。
- 卡點事實：`scripts/check/decode_board_2026-09-29.yaml` L25-4 的 `state`
  （`is_mem_shared`／61% `draft=0`／width=8 那趟 `k_eff` 1.590）。
