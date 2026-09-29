# 250 / 25 攻關：子目標 × options 對照表（2026-09-29）

> **唯一來源**：`scripts/check/decode_board_2026-09-29.yaml`（15 個子目標各寫死 `options.{profile,arms,knobs,cli,no_knob,note}`）。
> 本頁由 `scripts/check/mindmap_subgoal_sync.py --report` 機械生成；**沒有任何一格是文件掃描推導出來的**（前一版 `PROFILE_OPTION_MAP_2026-09-29` 已刪除）。

## 統計

- 子目標 **16** 格；52 條裡歸屬到某一格的 **25** 條，未歸屬 **27** 條。
- 有被測 option 的子目標 **11** 格；明寫「沒有旋鈕」的 **6** 格。
- 機械缺口：**0**

## 逐格對照（子目標 → options → 立項卡 → 節點）

| 子目標 | profile | 被測 option | 儀器／候選 | CLI | arm（可複製） | 立項卡 | 節點（白皮書） |
|---|---|---|---|---|---|---|---|
| **L20-1**<br>底（S1 的 step）校準 | `prod-new` | `CGC_SEG_BATCH=1`；`CGC_B_SCHEME=1`；`CGC_SLOT_TABLE_GPU=1` | `CGC_MISS_MASK=1`；`CGC_MISS_MASK_DBG=1`；`CGC_MISS_MASK_COST=1` | — | `prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1` | `e-s1-base-calibrate-2026-09-29.yaml` | [exp-prodnew-decode20-g4miss](briefs/exp-prodnew-decode20-g4miss.md)<br>[s1-segbatch](briefs/s1-segbatch.md)<br>[exp-s-retro](briefs/exp-s-retro.md) |
| **L20-2**<br>武裝 G3（CGC_ZERO_SLOT） | `prod-new` | `CGC_ZERO_SLOT=1`；`CGC_SEG_BATCH=1`；`CGC_SLOT_TABLE_GPU=1` | — | — | `prod-new:CGC_SEG_BATCH=1;CGC_SLOT_TABLE_GPU=1`<br>`prod-new:CGC_SEG_BATCH=1;CGC_SLOT_TABLE_GPU=1;CGC_ZERO_SLOT=1` | `e-s1-g3-zeroslot-2026-09-29.yaml` | [exp-prodnew-decode20-g4miss](briefs/exp-prodnew-decode20-g4miss.md)<br>[miss-3a](briefs/miss-3a.md)<br>[s1-probe](briefs/s1-probe.md) |
| **L20-3**<br>重算本體 B（靜態 ggml_acc） | `prod-new` | —<br>⚠ 本格沒有 env 旋鈕：B 是靜態寬度 ggml_acc，要動 src/。前置的儀器已經跑完（見上）⇒ <b>前置否證，不必再印</b>。 | `CGC_MISS_MASK_HIST=1` | — | `prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1;CGC_MISS_MASK_HIST=1` | `e-b-accpatch-feasibility-2026-09-29.yaml` | [exp-prodnew-decode20-g4miss](briefs/exp-prodnew-decode20-g4miss.md)<br>[miss-3c](briefs/miss-3c.md) |
| **L20-4**<br>fill 那一個 term 的定價 | `prod-new` | `CGC_EB_NOFILL=1` | `CGC_EB_TIMER=1` | — | `prod-new:CGC_EB_NOFILL=1` | `e-fill-term-price-2026-09-29.yaml` | [io-nofill](briefs/io-nofill.md) |
| **L20-5**<br>餵料搬家（P1） | `prod-new` | `CGC_SPAC_DBG=1`；`CGC_PREFETCH_SRC=hist`；`CGC_PREFETCH_WINDOW=4` | — | — | `prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1;CGC_SPAC_DBG=1`<br>`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1;CGC_MISS_MASK=1;CGC_MISS_MASK_DBG=1;CGC_MISS_MASK_COST=1;CGC_PREFETCH_SRC=hist;CGC_PREFETCH_WINDOW=4` | `exp-singlesubmit-fillahead.yaml` | [exp-singlesubmit-fillahead](briefs/exp-singlesubmit-fillahead.md)<br>[miss-3b](briefs/miss-3b.md) |
| **L20-6**<br>替代機制：非同步 fill ＋ 只補算 | `prod-new` | —<br>⚠ 已結案（CLOSED — 不做）：收益 +2.6~4.6% < MDD 8.5%，且要動 M1/M2/M3 護欄 ⇒ 本格沒有要跑的臂，只留登記。 | — | — | `prod-new` | `e-asyncfill-recompute-2026-09-28.yaml` | [s1-asyncgather](briefs/s1-asyncgather.md) |
| **L20-7**<br>prefetch 的兩個前置分支 | `prod-new` | `CGC_RHO_PROBE=1`；`CGC_PREBIND_PROBE=1` | `CGC_PREBIND_PROBE_VERBOSE=1`；`CGC_RHO_PROBE_LATE=1` | — | `prod-new:CGC_RHO_PROBE=1`<br>`prod-new:CGC_PREBIND_PROBE=1` | `e-prefetch-prereq-2026-09-29.yaml` | [cache-rho](briefs/cache-rho.md)<br>[cache-prebind](briefs/cache-prebind.md) |
| **L20-8**<br>churn 判詞在交付口徑的重檢 | `prod-new` | `CGC_S1_TABLE_CHURN=1`；`CGC_SLOT_TABLE_GPU=1` | `CGC_GPU_TIMING=1`；`CGC_DECODE_PROFILE=1` | — | `prod-new:CGC_S1_TABLE_CHURN=1;CGC_SLOT_TABLE_GPU=1;CGC_GPU_TIMING=1;CGC_DECODE_PROFILE=1` | `exp-churn-delivery-630.yaml` | [exp-churn-delivery-630](briefs/exp-churn-delivery-630.md) |
| **L20-9**<br>C 軸唯一「活著」的一格 | `prod-new` | — | `CGC_MMV_NSG=8`；`CGC_MMV_FUSE=1` | — | `prod-new` | `e-shape-knobs-2026-09-29.yaml` | [shape-knobs](briefs/shape-knobs.md) |
| **L20-10**<br>格子判別：量測起點 vs batch | `prod-new` | —<br>⚠ 本格沒有既有旋鈕可動：<code>batch</code>／<code>prompt</code>／<code>warm_skip</code> 都是測試卡 §2.5 的<b>嚴格維度</b>（改了 fail-closed 拒跑），現有孿生機制只開放 <code>reps</code>／<code>rep_split</code> ⇒ 這是一個 <b>cell 定義的決定</b>，不是一次跑得動的實驗。 | — | — | `prod-new` | `e-cell-discriminate-2026-09-29.yaml` | [exp-caliber-calibration](briefs/exp-caliber-calibration.md)<br>[sys-window](briefs/sys-window.md) |
| **L25-1**<br>第六條軸：改「每 token 的 bytes 或 steps」 | `prod-new` | `--spec-type=ngram-simple`；`--spec-ngram-simple-size-n=4`；`--spec-ngram-simple-size-m=8`；`--spec-ngram-simple-min-hits=2` | `CGC_SERVER_LOAD_MODE=mmap` | — | `prod-new` | `e-sixth-axis-2026-09-29.yaml` | [m-decode25](briefs/m-decode25.md)<br>[exp-caliber-calibration](briefs/exp-caliber-calibration.md) |
| **L25-2**<br>讓某一格超過自己的已量上界 | `prod-new` | —<br>⚠ 本格沒有自己的旋鈕，而<b>唯一被指名過的機制（B 半補丁）已被量死</b>：價目漏掉備援重算 320 列 ＝ <b>8.09 ms</b>（同儀器實測；靜態寬度被正確性逼成 k=8）⇒ <code>39.0 ＋ 9.7~13.8 ＝ 48.7–52.8 ms</code>。⇒ 要嘛**新的**上界外機制，要嘛維持判死。<br>★ <b>它的復活門（那行逐層直方圖）09-29 已經跑過，且已關閉</b>：交付 cell 穩態每層峰值 <b>6</b>（不是 2）、≤2 只覆蓋 64% 的步；連把設計點降到峰值 6，重算 <b>6.07 ms</b> 都 > fill <b>3.955</b> ⇒ <b>不是「還沒量」，是量了、不成立</b>（<code>docs/MISSHIST_REVIVAL_GATE_2026-09-29.md</code>）。 | — | — | `prod-new` | `e-beyond-ceiling-2026-09-29.yaml` | [m-decode25](briefs/m-decode25.md) |
| **L25-3**<br>乾淨窗口（C7） | `prod-new` | —<br>⚠ 窗口條件不是旋鈕：attribution=none ∧ thermal NOMINAL ∧ reps≥3。CGC_WINDOW_OVERRIDE 是繞過閘門的開關，不能當本格的 option。 | — | — | `prod-new` | `e-clean-window-2026-09-29.yaml` | [sys-window](briefs/sys-window.md) |
| **L25-4**<br>MTP（C6）：唯一「乘」的軸 | `prod-new` | `CGC_SERVER_MTP=1`；`CGC_SERVER_LAYER_CAPS=40-40:16`；`CGC_DRAFT_CTX_ALIGN=1`；`CGC_DRAFT_SMALL_BATCH=1` | `CGC_MTP_PERF=1` | `--spec-type draft-mtp`；`--spec-draft-n-max 1` | `prod-new:CGC_SERVER_MTP=1;CGC_SERVER_LAYER_CAPS=40-40:16;CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1` | `exp-m-draft-cost.yaml`<br>`e-mtp-k-bracket-2026-09-28.yaml` | [exp-m-draft-cost](briefs/exp-m-draft-cost.md)<br>[mtp-ksweep](briefs/mtp-ksweep.md) |
| **L25-5**<br>MTP 的產品化上限 | `prod-new` | `CGC_SERVER_MTP=1` | `CGC_MTP_PERF=1` | `--spec-type draft-mtp`；`--spec-draft-n-max 1` | `prod-new:CGC_SERVER_MTP=1;CGC_SERVER_LAYER_CAPS=40-40:16;CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1;CGC_MTP_PERF=1` | `e-mtp-m-acc-2026-09-29.yaml` | [mtp-2x](briefs/mtp-2x.md)<br>[mtp-caliper](briefs/mtp-caliper.md)<br>[mtp-instrument](briefs/mtp-instrument.md)<br>[mtp-accept](briefs/mtp-accept.md) |
| **L25-6**<br>口徑認證 | `prod-new` | —<br>⚠ 沒有新旋鈕：本格要的是加大配對 n（-r）到能認證 16.4% 的飄移，或證明飄移是視窗機械造成。 | — | — | `prod-new` | `exp-k3-pair-cert.yaml` | [k3-swing](briefs/k3-swing.md)<br>[score-leaderboard](briefs/score-leaderboard.md) |

## 52 條逐條處置（每條都要落在子目標／已認證／已定案／已作廢之一）

| 條目 | 級 | 處置 | 指向 | 理由 |
|---|---|---|---|---|
| [G4：元素級融合 kernel](briefs/g4.md) | 4 | 作廢／判死／歷史 | `—` | G4 元素級融合 kernel 判死；已認證的是它的閘（C2），不是這條實作 |
| [CGC_LAYER_AHEAD_PREFETCH（提前一層）](briefs/io-layer-ahead.md) | 4 | 作廢／判死／歷史 | `—` | 判死：CGC_LAYER_AHEAD_PREFETCH（提前一層） |
| [M-PF：背景 neighbour prefetch](briefs/io-mpf.md) | 4 | 作廢／判死／歷史 | `—` | 判死：M-PF 背景 neighbour prefetch |
| [M-W：WORKERS 8→2](briefs/io-mw.md) | 4 | 作廢／判死／歷史 | `—` | 判死：WORKERS 8→2 |
| [IO 請求形狀（合併 pread）](briefs/io-shape.md) | 4 | 作廢／判死／歷史 | `—` | 判死：IO 請求形狀（合併 pread） |
| [K0–K5 系列（融合／小 op 群）](briefs/k-series.md) | 4 | 作廢／判死／歷史 | `—` | K0–K5 全結案（融合／小 op 群判死） |
| [M3／M4／M5 離開條件（09-17 期）](briefs/m3.md) | 4 | 作廢／判死／歷史 | `—` | M3／M4／M5 離開條件（09-17 期）—— 歷史期的進入條件，已過 |
| [RSL-MTP（draft top-8 ⊆ 已付費並集）＋ 練 draft head](briefs/mtp-rsl.md) | 4 | 作廢／判死／歷史 | `—` | 判死（需要練 draft head；收益不成立） |
| [verify residency thrash ／ 池配額 ／ prefetch 開關](briefs/mtp-verify-opt.md) | 4 | 作廢／判死／歷史 | `—` | 判死（verify residency thrash／池配額／prefetch 開關三條都敗） |
| [跨線／其他產品（Wan2.2、HarmonyOS、Windows client、Colibri、Unified IR…）](briefs/na-crossline.md) | na | 作廢／判死／歷史 | `—` | 非實驗：跨線／其他產品（Wan2.2、HarmonyOS、Windows client、Colibri、Unified IR） |
| [expert cache 血統設計（09-05~09-09 期）](briefs/na-design.md) | 3b | 作廢／判死／歷史 | `—` | 非實驗：expert cache 血統設計（09-05~09-09 期） |
| [入口／索引／決策頁（非實驗）](briefs/na-entry.md) | na | 作廢／判死／歷史 | `—` | 非實驗：入口／索引／決策頁 |
| [舊口徑數據報告（Gemma4／MTP_BENCHMARK_WIN8GB／TPOT 路線圖）](briefs/na-olddata.md) | 4 | 作廢／判死／歷史 | `—` | 作廢：舊口徑數據（Gemma4／MTP_BENCHMARK_WIN8GB／TPOT 路線圖） |
| [S1 早期診斷系列（09-16/17）](briefs/s1-refuted.md) | 4 | 作廢／判死／歷史 | `—` | 作廢：S1 早期診斷系列（09-16/17），已被 slot owner 否證取代 |
| [prefill ≥ 250（交付 cell）](briefs/m-prefill250.md) | 2 | 已認證 | `C1` | prefill ≥250 的那個里程碑本體；9 次 launch ≥250、最高 296.24 |
| [① 攻關成功（pp≥250 ∧ tg>12.57）](briefs/m-total.md) | 1 | 已認證 | `C1` | 里程碑配對：prefill 這一半是 C1，decode 那一半（>12.57）未達 ⇒ 只認 C1 |
| [頻寬屋頂／dense GEMV 上界](briefs/c-bandwidth.md) | 3a | 已定案（約束／基準） | `—` | 頻寬屋頂／dense GEMV 上界：C 軸的上界，已證偽的候選都在這裡 |
| [device span 歸因（最大一塊時間）](briefs/c-device-span.md) | 3a | 已定案（約束／基準） | `—` | device span 歸因（56–61%）：C 軸的背景約束，不是待辦 |
| [cb 口徑定讞（42 vs 74）](briefs/cache-cb.md) | 3b | 已定案（約束／基準） | `—` | cb 口徑定讞（42 vs 74）：現行 `cb` 桶的讀法就是它 |
| [C：有效帶寬 13.65 → ≥29 GB/s（16／25 t/s）](briefs/exp-c-eff-bandwidth.md) | 3a | 已定案（約束／基準） | `—` | C：有效帶寬 13.65→≥29 GB/s 的目標線；C 軸背景，不進 L20/L25 |
| [C：讀取發行開銷（重驗 io-shape）](briefs/exp-c-read-issue.md) | 3a | 已定案（約束／基準） | `—` | C：讀取發行開銷（重驗 io-shape）；同上，背景 |
| [S2：段邊界免等（天花板 18.7 t/s）](briefs/exp-s2-overlap.md) | 3a | 已定案（約束／基準） | `—` | S2 段邊界免等：已結（FAIL 附機制，殘項是 Metal 側延遲） |
| [G1：可達上界 / G1-G7 sweep](briefs/g1.md) | 3a | 已定案（約束／基準） | `—` | G1 可達上界／G1-G7 sweep：上界算術已結，數字只當背景 |
| [IOCACHE 約束承認 ＋ 段邊界 S2](briefs/io-constraint.md) | 4 | 已定案（約束／基準） | `—` | IOCACHE 約束承認（＋段邊界 S2）：已承認的約束 |
| [池大小掃描（8→4→2 GiB）](briefs/io-poolsize.md) | 3b | 已定案（約束／基準） | `—` | 池大小已仲裁：8 GiB 定案，3／4 GiB 不可選（衝突是儀器造成的） |
| [K3 邊際單價（dispatch 成本）](briefs/k3-price.md) | 3b | 已定案（約束／基準） | `—` | K3 邊際單價（dispatch 成本）已量；上界算術的常數 |
| [swap 結構修復（L0–L4 + P0/P1/P2）](briefs/sys-swap.md) | 3b | 已定案（約束／基準） | `—` | swap 結構修復（L0–L4）：現在 launch swap 0 的來源 |
| [prebind／方案 A（預指派 slot）](briefs/cache-prebind.md) | 3a | 整合進子目標 | `L20-7` | prebind／方案 A（預指派 slot）—— 另一條前置分支 |
| [ρ 路線（按層批次化 prefetch）](briefs/cache-rho.md) | 3a | 整合進子目標 | `L20-7` | ρ 按層批次化 prefetch（覆蓋 0.849、每步付 4.76 ms） |
| [口徑＋k=3 飄移認證（16.4% 噪聲底）](briefs/exp-caliber-calibration.md) | 3a | 整合進子目標 | `L25-1` | 口徑＋k=3 飄移認證；第六條軸要用的量測基準 |
| [口徑＋k=3 飄移認證（16.4% 噪聲底）](briefs/exp-caliber-calibration.md) | 3a | 整合進子目標 | `L20-10` | §56：跨 cell 的兩個數字不可互比 ⇒ 判別子目標（口徑本身就是這一格） |
| [在**今天的交付口徑**上（`prod-new` profile、`…](briefs/exp-churn-delivery-630.md) | 3a | 整合進子目標 | `L20-8` | 在交付口徑上重檢 churn；L20-8 的產物 |
| [M：攤薄係數 m 0.474 → ≤0.073](briefs/exp-m-draft-cost.md) | 3a | 整合進子目標 | `L25-4` | 攤薄係數 m 的成本曲線；MTP 決策的價格表 |
| [在**認可入口**（`harness bench`、`prod-ne…](briefs/exp-prodnew-decode20-g4miss.md) | 3a | 整合進子目標 | `L20-1` | L20 的主節點；G4 閘的權威讀數（中位 6.09%）就是它的產物 |
| [在**認可入口**（`harness bench`、`prod-ne…](briefs/exp-prodnew-decode20-g4miss.md) | 3a | 已認證 | `C2` | G4 重算工作清單中位 6.09% ≤25%，已認證 |
| [G0–G7 序列化／調度消減（41 段→1 段、縮 union、藏 …](briefs/exp-s-retro.md) | 3a | 整合進子目標 | `L20-1` | G0–G7 的序列化回顧：同一條軸的歷史與作廢清單，決定 L20-1 只認哪一端 |
| [在單次提交臂上，**把每一步的 union 不經 hook 交給預取…](briefs/exp-singlesubmit-fillahead.md) | 3a | 整合進子目標 | `L20-5` | 單次提交臂上把 union 交給預取；P1 餵料的載體 |
| [CGC_EB_NOFILL 診斷臂（fill 成本）](briefs/io-nofill.md) | 3a | 整合進子目標 | `L20-4` | CGC_EB_NOFILL 診斷臂：fill 那一個 term 的定價就靠它（3.955→0.206 ms/step） |
| [k=3 的 1.43× 飄移定位 ＋ 配對認證](briefs/k3-swing.md) | 3a | 整合進子目標 | `L25-6` | k=3 的 1.43× 飄移定位：16.4% 的底噪就是它 |
| [decode ≥ 25（M-25）](briefs/m-decode25.md) | 4 | 整合進子目標 | `L25-1` | decode ≥25 的主節點（tier 4）；§51 之後只剩 L25-1 一條活路（L25-2 唯一被指名的機制 B 已被量死） |
| [3a：未填充 expert 貢獻歸零（MISS_MASK / ZERO_MISS）](briefs/miss-3a.md) | 3b | 整合進子目標 | `L20-2` | 未填充 expert 貢獻歸零（MISS_MASK／ZERO_SLOT）—— L20-2 武裝 G3 的正確性前提 |
| [3b：fill 觸發點搬出 hook ＋ batch 化](briefs/miss-3b.md) | 4 | 整合進子目標 | `L20-5` | fill 觸發點搬出 hook ＋ batch 化 —— 正是 L20-5「餵料搬家」要搬的那一段 |
| [3c：per-expert 重算 kernel](briefs/miss-3c.md) | 4 | 整合進子目標 | `L20-3` | per-expert 重算 kernel（已判 0）—— L20-3 的 B 是同一件事的靜態版本，判詞要一起讀 |
| [MTP 到 2× 的邊界（攤薄算術）](briefs/mtp-2x.md) | 3b | 整合進子目標 | `L25-5` | 2× 邊界算術（S=(1+a·k)/(1+m·k)，上界 a/m） |
| [accept rule／dynamic-k／n_max 調整](briefs/mtp-accept.md) | 4 | 整合進子目標 | `L25-5` | accept rule／dynamic-k／n_max：L25-5 的「acc 那一條目標」 |
| [MTP 口徑與混淆定位（pool／跨啟動／順序）](briefs/mtp-caliper.md) | 3b | 整合進子目標 | `L25-5` | MTP 口徑與混淆定位：m 的每一個讀數都要先過它 |
| [MTP 儀器化（接進 llama-bench）](briefs/mtp-instrument.md) | 3b | 整合進子目標 | `L25-5` | MTP 儀器化（接進 llama-bench）—— 沒有它，L25-5 的 m 量不出來 |
| [MTP k-sweep（verify batch T 成本曲線）](briefs/mtp-ksweep.md) | 3a | 整合進子目標 | `L25-4` | k-sweep（verify batch T 成本曲線）：k 端點包夾的來源 |
| [異步 gather 流水線（單段＋miss 後台補＋局部重算）](briefs/s1-asyncgather.md) | 3a | 整合進子目標 | `L20-6` | 異步 gather 流水線；L20-6 已排除，這條是它的機制紀錄 |
| [S1 探針臂：slot table 放 GPU（數值身分）](briefs/s1-probe.md) | 3b | 整合進子目標 | `L20-2` | slot table 放 GPU 的數值身分（576/576）：讓 leaf 被建的前置，G3 的最小武裝就是它 |
| [單段提交（41 段 → 1 段）](briefs/s1-segbatch.md) | 3a | 整合進子目標 | `L20-1` | 單段提交本體 —— L20-1 要校準的那個 step 就是它 |
| [成績排行榜（最高配置＋檢驗檔）](briefs/score-leaderboard.md) | 2 | 整合進子目標 | `L25-6` | 成績排行榜；「目前最好」的定義衝突在這裡結清 |
| [成績排行榜（最高配置＋檢驗檔）](briefs/score-leaderboard.md) | 2 | 已認證 | `C3` | 「目前最好」＝交付錨點 11.703 t/s（三扇門全過） |
| [shape／knob 世界模型（14–15 個旋鈕的邊界）](briefs/shape-knobs.md) | 3b | 整合進子目標 | `L20-9` | shape／knob 世界模型：C 軸唯一還活著的一格 |
| [server 窗口／box 准入（單一來源閘門）](briefs/sys-window.md) | 3b | 整合進子目標 | `L25-3` | box 准入單一來源閘門 —— 乾淨窗口那一格就是它 |
| [server 窗口／box 准入（單一來源閘門）](briefs/sys-window.md) | 3b | 整合進子目標 | `L20-10` | 量測起點（同一行程裡 pp 測試的有無）屬於窗口條件 |

## 未歸屬任何子目標的條目（＝已認證／已定案／已作廢）

- [prefill ≥ 250（交付 cell）](briefs/m-prefill250.md)（`2`）——已認證（C1）
- [① 攻關成功（pp≥250 ∧ tg>12.57）](briefs/m-total.md)（`1`）——已認證（C1）
- [池大小掃描（8→4→2 GiB）](briefs/io-poolsize.md)（`3b`）——已定案（約束／基準）
- [IO 請求形狀（合併 pread）](briefs/io-shape.md)（`4`）——作廢／判死／歷史
- [M-W：WORKERS 8→2](briefs/io-mw.md)（`4`）——作廢／判死／歷史
- [CGC_LAYER_AHEAD_PREFETCH（提前一層）](briefs/io-layer-ahead.md)（`4`）——作廢／判死／歷史
- [M-PF：背景 neighbour prefetch](briefs/io-mpf.md)（`4`）——作廢／判死／歷史
- [S1 早期診斷系列（09-16/17）](briefs/s1-refuted.md)（`4`）——作廢／判死／歷史
- [RSL-MTP（draft top-8 ⊆ 已付費並集）＋ 練 draft head](briefs/mtp-rsl.md)（`4`）——作廢／判死／歷史
- [verify residency thrash ／ 池配額 ／ prefetch 開關](briefs/mtp-verify-opt.md)（`4`）——作廢／判死／歷史
- [cb 口徑定讞（42 vs 74）](briefs/cache-cb.md)（`3b`）——已定案（約束／基準）
- [K3 邊際單價（dispatch 成本）](briefs/k3-price.md)（`3b`）——已定案（約束／基準）
- [K0–K5 系列（融合／小 op 群）](briefs/k-series.md)（`4`）——作廢／判死／歷史
- [G4：元素級融合 kernel](briefs/g4.md)（`4`）——作廢／判死／歷史
- [device span 歸因（最大一塊時間）](briefs/c-device-span.md)（`3a`）——已定案（約束／基準）
- [頻寬屋頂／dense GEMV 上界](briefs/c-bandwidth.md)（`3a`）——已定案（約束／基準）
- [G1：可達上界 / G1-G7 sweep](briefs/g1.md)（`3a`）——已定案（約束／基準）
- [IOCACHE 約束承認 ＋ 段邊界 S2](briefs/io-constraint.md)（`4`）——已定案（約束／基準）
- [swap 結構修復（L0–L4 + P0/P1/P2）](briefs/sys-swap.md)（`3b`）——已定案（約束／基準）
- [M3／M4／M5 離開條件（09-17 期）](briefs/m3.md)（`4`）——作廢／判死／歷史
- [入口／索引／決策頁（非實驗）](briefs/na-entry.md)（`na`）——作廢／判死／歷史
- [跨線／其他產品（Wan2.2、HarmonyOS、Windows client、Colibri、Unified IR…）](briefs/na-crossline.md)（`na`）——作廢／判死／歷史
- [expert cache 血統設計（09-05~09-09 期）](briefs/na-design.md)（`3b`）——作廢／判死／歷史
- [舊口徑數據報告（Gemma4／MTP_BENCHMARK_WIN8GB／TPOT 路線圖）](briefs/na-olddata.md)（`4`）——作廢／判死／歷史
- [C：有效帶寬 13.65 → ≥29 GB/s（16／25 t/s）](briefs/exp-c-eff-bandwidth.md)（`3a`）——已定案（約束／基準）
- [C：讀取發行開銷（重驗 io-shape）](briefs/exp-c-read-issue.md)（`3a`）——已定案（約束／基準）
- [S2：段邊界免等（天花板 18.7 t/s）](briefs/exp-s2-overlap.md)（`3a`）——已定案（約束／基準）

## 缺口（機械判定）

（無）

---

本檔由 `scripts/check/mindmap_subgoal_sync.py` 機械生成；改設定請改 `decode_board_2026-09-29.yaml` 後重跑，勿直接編輯本檔。
