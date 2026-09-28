# MTP ON bit-identical 修復立項（2026-09-28）

> 🔴 **狀態：已關閉（operator 2026-09-28 14:2x 追認）** —— 立項目標（A1–A5 全過）**未達成**，
> 且已觸發本卡 §3／§6 自己寫的「失敗定義」⇒ **H8 成立（源碼級證明）**。
> **收尾判定全文 ⇒ `docs/MTP_BITIDENT_CLOSEOUT_2026-09-28.md`**
> （A1–A5 逐條結算、`n_rs_seq` 源碼級證明、P2-b 負收益評估）
>
> ✅ **後繼立項：operator 同批裁定開立「MTP 口徑重定義」**
> ⇒ **`docs/MTP_CALIBER_REDEFINE_CHARTER_2026-09-28.md`**（B1–B4，0 build，純標註；
> 把 `CGC_SERVER_MTP=1` 定位為**另一個輸出函數**，交付口徑維持 MTP off）
>
> 提出：本線（`線A (ace)`）· 依據 `docs/MTP_ON_OUTPUT_ABLATION_2026-09-28.md`、
> `docs/MTP_ON_VERIFY_2026-09-28.md`、`docs/MTP_ON_FIX_RESULT_2026-09-28.md`

---

## 0. 一句話 + 驗收判據

**目標**：讓 MTP on 在權威 cell 上與 MTP off 做到 **M1/M2/M3 bit-identical**，使 ON 的 t/s
重新變得**可與 OFF 互比**（目前不可比，見 §1）。

**驗收（全部要過，缺一不可）**：

| # | 判據 | 目前 |
|---|---|---|
| A1 | `m123_oracle_gate.py` 的 **M1 / M2 全 PASS**（且 `only_A=0 only_B=0`，即兩側 dump 同形狀） | ❌ OFF/ON 形狀不同，M2 失敗 |
| A2 | 敏感探針三點的 logit **逐位相同**：`pos=1` / `pos=209` / `pos=213` | ❌ `pos=209` OFF 19.22745 vs ON 19.25835 |
| A3 | greedy 下 `probe_answer` **逐字相同** | ❌ 第 8 個 token 就換詞 |
| A4 | ON 自己仍可重現（防止「修完變隨機」） | ✅ 176/176 bit-identical |
| A5 | 修完才做性能配對重跑，且**窗口乾淨**（`attribution` 非 swap/thermal） | ❌ 12:32/12:34 兩臂都是 `both` |

**護欄**：M1/M2/M3 是**護欄不是獎勵** —— 不允許用放寬判據、換 cell、換 prompt 的方式「達標」。

---

## 1. 為什麼要立項（兩條理由，第二條是新的）

1. **慢**：同 build `e5d1c0f14`，OFF tg 中位 **11.92** vs ON **7.34**（ON 的 rep1 是**系統性暴走**
   0.39，不是噪音）。但因為兩臂盒況都是 `attribution='both'`，**「慢多少」不可定價**。
2. **而且做的不是同一份工作**：greedy 下兩側答案不同，且 ON 自己 176/176 可重現 ⇒
   兩側是**確定的、但不同的輸出函數** ⇒ **ON 與 OFF 的 t/s 不在同一個座標系**。
   ⇒ 只要第 2 條不解，第 1 條的數字永遠不能用。

---

## 2. 已確立的事實（每條都有實跑，不要重做）

### 2.1 分歧的定位（數字）

對齊鍵用**絕對位置 `pmax + token_idx`**（gate 自帶的鍵會塌縮，見 §2.4）：

| 位置 | OFF | 所有 ON 變體（7 個） | 判讀 |
|---|---|---|---|
| `pos=1`（prefill 尾） | 27.57889 | **27.57889（全臂 bit-identical）** | **prefill／KV 完全精確** |
| **`pos=209`（第一個 decode）** | **19.22745** | **19.25835**（差 0.0309 ≈ 0.16%） | **從第一個 decode 就分歧** |
| `pos=213` 起 | — | — | token 選擇也分歧 |

### 2.2 ★ 本輪新排除（零成本，從既有 dump 讀出）

這三條**改變了主攻方向**，立項前必須先知道：

- ⛔ **「verify 是多 token batch ⇒ 歸約順序不同」已被證偽**。
  OFF 全 50 行 `n_tokens=1`；ON 的第一個 decode（step=1, `pmax=209`）**也是 `n_tokens=1`**，
  logit 卻已經是 19.25835。多 token（4）只出現在後面的 verify 批。
- ⛔ **「draft forward 污染了共享狀態」已被證偽**。
  ON 的 `ctx_type='MTP'` 行最小 step = **2**，而第一個 DEF decode 是 **step=1**
  ⇒ **早於它的 draft forward 有 0 次** ⇒ 差異在**任何 draft forward 之前**就已存在。
- ⛔ **pool 布局／slot 數不是成因**：兩側 `n_slots` 都是 **143**（`expert_cache_pool_capacity>0`
  時 `n_slots` 直接取 capacity，不受 `max_layer` 40→41 影響）。`CGC_SERVER_LAYER_CAPS=40-40:16`
  （大幅緩解 blk.40 擠壓）也**沒改變 logit**。

⇒ **結論：差異由 model load／ctx 建立階段決定，與 speculative 機制本身無關。**
⇒ 修的方向不是「讓 draft 不影響 target」，而是「**讓『多載入 blk.40』這件事不影響 target 前 40 層的數值**」。

### 2.3 已排除的 8 個 env 鍵（ablation 結案）

`PREFIX_REUSE_CKPT` / `VERIFY_DECODE` / `DRAFT_DECODE` / `WARM_NPAST` / `NO_PREFETCH` /
`NO_SEQ_RM_PROBE` / `MTP_NO_WARMUP` / `LAYER_CAPS` —— 逐鍵關掉後 logit 與答案**全部不變**。

⚠ 其中兩點要記住：
- `run_server.sh:306-315` 白紙黑字寫 `CGC_PREFIX_REUSE_CKPT`「MTP-on 下非 bit-neutral」，
  但**關掉它完全不變** ⇒ **記載不等於觀測**。
- **E5（OFF 側加 `40-40:256`）是無效實驗**：OFF 根本沒載 blk.40，設它的 cap 是 no-op。
  ⇒ 「在 OFF 側複製 ON 的 index 數」這條反向驗**做不到**，只能從 ON 側拆。

### 2.4 儀器坑（會毀掉結論）

- ⛔ **跨臂 M1/M2 只在「兩側 dump 同形狀」時可讀**。ON 的 101 行 DEF 只有 **27 個唯一 `pmax`**
  （verify 批一次解 4 token，鍵塌縮）⇒ 那個 `M1 1/14` 是假象。**先看 `only_A/only_B` 是否為 0**。
  同形狀時 gate 是好的（E5 拿到 50/50 PASS）。
- ⚠ `config_diffs` 的 30 項裡 **22 項是 argv 位移的假差異**；要比 env 用 `.cap` 的
  `resolved.ENV` 全差分（真實差異就是 8 個）。
- ⚠ 兩個模型名不同其實是 **symlink 同一個檔案**，不是混淆。
- ⚠ `--ref` / `--write-ref` 必須放 **repo 內**（放 `/tmp` 會撞 `relative_to` 崩）。

---

## 3. 假設排位（只列還活著的）

| 假設 | 內容 | 判定方式 | 成本 |
|---|---|---|---|
| **H5（首要）** | target ctx 在 `SERVER_MTP=1` 時**以不同方式建立**（KV 形狀／`n_seq_max`／fixed-shape flag／`--spec-draft-ngl` 帶來的資源配置），使**第一次 decode** 就走不同數值路徑 | P0-2／P0-3 | 0 build |
| **H6** | llama.cpp spec 路徑在 target 首次 decode **之前**就改了 `n_past`／graph 形狀（prefill 不受影響，故 prefill 尾仍 identical） | P0-1（逐層定位） | 要 build |
| **H7** | `expert_index` 30720→31488 讓某個**查表／分區**函數的結果對前 40 層也不同（不是 slot 數，是映射） | P0-4 | 0 build |
| **H8（兜底）** | 差異是 spec 路徑**本質上不可避免**的（開 spec 就換一條 decode 路徑）⇒ **bit-identical 原理上不可達** | P0 三趟皆無變化 ⇒ 成立 | 0 build |

**H8 是本立項的退出條件**：若 P0 三趟都無法讓 logit 動一分一毫，且 P1 定位顯示分歧在
第 0 層就已存在 ⇒ 判定不可達 ⇒ **關閉本立項**，另開「口徑重定義」立項（把 MTP 明確定位為
「另一個輸出函數」，不再當純加速開關）。**不要為了達標去放寬 A1–A3。**

---

## 4. 階段與實驗序列（**嚴格順序，不要並行** —— 每步都要能證偽）

### P0：零 build 判定（約 3 趟 × 3 分鐘，本線可立刻跑）

> ✅ **已跑完（2026-09-28 13:2x–13:4x）⇒ 結論：進 P1**。三趟（`DRAFT_NGL=0`／`MTP_N_MAX=1`／
> pool 8→4 GiB）**logit 全無變化**（`pos=209` 恆為 19.258354，row hash 逐位相同）；
> pool 加大方向（12/10 GiB）在 16 GB 上 OOM 不可行 ⇒ **H5、H7 證偽，H6 升為首要**。
> 全文 `docs/MTP_BITIDENT_P0_RESULT_2026-09-28.md`。P1 前建議先跑那份 §4 的 **P0-5**（`N_MAX=0`，1 趟）。

前提：8080 無 listener、無別線行程（這是硬性閘門，見 §7）。判據**只看 `pos=209` 的 logit**
與 `probe_answer`，不看 t/s。

```sh
cd /Users/alexchuang/Documents/flashkv-devserver
P='請用繁體中文簡短介紹台灣的三個觀光景點，每點一行。'

# P0-2（H5）：draft 全踢到 CPU，看 target 第一個 decode 是否回到 19.22745
/opt/homebrew/bin/python3 scripts/check/m123_oracle_gate.py --profile prod-new \
  --env CGC_SERVER_MTP=1 --env CGC_SERVER_TEMP=0 --env CGC_SERVER_DRAFT_NGL=0 \
  --probe-prompt "$P" --probe-max-tokens 48 \
  --dump Backup/m123_oracle_gate/dump_p02_ngl0.jsonl \
  --ref Backup/m123_oracle_gate/mtp_bitident_greedy_off.jsonl \
  --allow-incomparable --tag p02-ngl0-20260928

# P0-3（H5）：draft 深度降到 1（第一次 decode 之前仍沒有 draft forward，屬交叉驗證）
#   若 logit 依舊 19.25835 ⇒ 再次確認「與 draft 深度無關」
  --env CGC_SERVER_MTP=1 --env CGC_SERVER_TEMP=0 --env CGC_SERVER_MTP_N_MAX=1   # tag p03-nmax1

# P0-4（H7）：把 pool 加大消除擠壓（若 logit 隨之變動 ⇒ 查表/布局敏感）
  --env CGC_SERVER_MTP=1 --env CGC_SERVER_TEMP=0 --env CGC_SERVER_EXPERT_CACHE_BYTES=12884901888
  # ⚠ 12 GiB 在 16 GB 盒上可能 OOM；若起不來就降到 10737418240，或直接標「不可行」
```

**P0 判讀表**：

| 結果 | 結論 | 下一步 |
|---|---|---|
| 任一趟 logit 回到 **19.22745** | H5/H7 該鍵**就是成因**，且是**設定問題不是機制問題** | 修 `run_server.sh` 的 MTP 分支（或加守門），回到 A1–A5 驗收 |
| 三趟 logit **全是 19.25835**（與既有 7 個變體一致） | 支持 H6／H8 | 進 P1（要 build） |

### P1：逐層定位（**要 build，要約窗口**）

在 target 的**第一次 decode**（`pmax=209`）上，逐層 dump logit／張量 hash，找**第一個分歧的層 `il*`**。
差分界在 「prefill 尾 identical、第一個 decode 就分歧」⇒ `il*` 會直接指出是哪個 op 分岔。

- 優先用既有儀器（`CGC_TENSOR_CAPTURE`／`CGC_S1_DBG` 一類的 hook），**不新增機制**。
- 產物：`Backup/m123_oracle_gate/per_layer_first_decode_{off,on}.jsonl`。
- 判讀：`il* == 0` ⇒ 走向 H8（進退出條件）；`il*` 在中間 ⇒ H6 成立，可修。

### P2：實作修復（**要 build**）

依 P0/P1 結果二選一，**不要同時做**：

- **P2-a（若 P0 命中）**：改 `scripts/run_server.sh` 的 MTP 分支（最小改動，不動引擎）。
  風險最低，但要確認改完不影響 draft 命中率與存活（跑 §0 的 A1–A5 + 一次 bench）。
- **P2-b（若走到 P1）**：改 `src/llama.cpp/src/`（候選：MTP block 的專家**延後入池／獨立區段**，
  讓 target 前 40 層的查表與映射與 MTP off 完全一致）。這是真正的引擎改動，需約窗口。

### P3：驗收 + 性能配對（完成才算修完）

1. A1–A5 全過（§0 表）。
2. 乾淨窗口的配對 bench：`--arm prod-new` vs `--arm 'prod-new:CGC_SERVER_LAYER_CAPS=40-40:16;
   CGC_DRAFT_CTX_ALIGN=1;CGC_DRAFT_SMALL_BATCH=1' --spec-type draft-mtp`，各 3 reps。
   **窗口不乾淨就不要寫入結論**（這是本線既有的量測紀律）。

---

## 5. 明確**不要**做的事

- ⛔ **不放寬 M1/M2/M3 來「達標」**（換 prompt、換 cell、只比前 N token 都不行）。
- ⛔ **不在 ON/OFF 混比 t/s** —— A1–A3 沒過之前，任何「MTP 加速 X%」都不可引用。
  （既有 `12.57`、m1 排行榜的 13.099 都屬此類，已標記不可當 OFF 基線。）
- ⛔ **不用 `CGC_METAL_FAIL_STOP=0`**（只是不 abort，回傳 stale output = garbage）。
- ⛔ **不降 `-p` / `-ub` 換存活**（cell 不再可比）。
- ⛔ **不一次改多個鍵**（ablation 已證明逐鍵才是唯一能歸因的方法）。
- ⚠ **不碰 `prod-new` 的 `CGC_SERVER_MTP=0` 預設** —— 交付口徑在修完之前**維持 MTP off**。

---

## 6. 成本、窗口、歸屬、回滾

| 項 | 內容 |
|---|---|
| P0 | ~10 分鐘，0 build，只佔 GPU（8080 閘門） |
| P1 | 1 次 build ＋ ~20 分鐘量測 |
| P2 | 1–N 次 build；P2-a 只動 `scripts/`（低風險），P2-b 動 `src/`（高風險） |
| P3 | ~15 分鐘 × 2 臂（要等乾淨窗口） |
| **歸屬** | 全部屬**本線**（`線A (ace)`：引擎層 `src/`／`scripts/check/`／decode 量測）。`agent_harness/` 不碰 |
| **視窗** | 動 build 前**必須**跑會 abort 的閘門：`lsof -nP -iTCP:8080 -sTCP:LISTEN` 與 `pgrep -fl 'run_ids_dst_capture\|decode_sweep\|llama-server'`，**檢查與動作在同一分支**。12:54 曾遇別線常駐 server（PID 86250）⇒ **不 kill、不換 port 硬跑**，等窗口 |
| **回滾** | P2-a：`git checkout scripts/run_server.sh`。P2-b：只改 `src/llama.cpp/src/` 的單一檔案，保留 diff。任何時候都可退回「MTP off」= 交付口徑不變 |
| **失敗定義** | P0 三趟無變化 + P1 顯示 `il*==0` ⇒ **關閉本立項**，改開「口徑重定義」立項 |

---

## 7. 立項待辦（勾選制）

- [x] **批准立項**（operator 已批准繼續；動 `src/` 的授權已使用 1 次：P1 的新儀器）
- [x] P0-2 / P0-3 / P0-4（0 build）→ **皆無變化 ⇒ 進 P1**（`MTP_BITIDENT_P0_RESULT_2026-09-28.md`）
- [x] P0-5（`CGC_SERVER_MTP_N_MAX=0`）→ ⛔ **不成立**：
  `GGML_ASSERT(n_outputs_max <= cparams.n_outputs_max)`（`llama-context.cpp:3057`），配置非法
- [x] **P1 逐層定位 → ✅ 根因已找到**（`docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md`）：
  **`cparams.n_rs_seq` 0 → >0** ⇒ `delta-net-base.cpp:494` 的 recurrent（GDN／conv）狀態
  從「單槽位原地寫回」換成「**K 槽位回滾**」⇒ 連計算份數都變了（`gdn` 30→60、`conv_input` 30→150）
  ⇒ **第一個 decode 就分歧**
- [x] **H8 源碼級證明（收尾輪）** ⇒ `docs/MTP_BITIDENT_CLOSEOUT_2026-09-28.md` §2：
  `common.h:395 need_n_rs_seq()` 對 MTP 型別**硬編碼回傳 `draft.n_max`**，而 `n_max=0` 非法
  ⇒ **沒有任何 config／env 旋鈕能「MTP on 且 `n_rs_seq=0`」⇒ P2-a 原理上不存在**
- [x] **ON 輸出不是品質缺陷**（收尾輪）：兩側 greedy 答案都通順、語義等價，ON 自身 176/176 可重現
  ⇒ 「另一個輸出函數」的定位成立，**不是拿品質換速度**
- [x] **P2-b 決策：不做**（收尾輪 §3）：修成也負收益（ON 方向性比 OFF 慢，且要動 recurrent kernel）
- [x] ~~依 P0 結果決定進 P1 或直接 P2-a~~ → P2-a 已證明不存在
- [x] ~~P1 逐層定位（約 build 窗口）~~ → 已完成（1 次 build）
- [x] ~~P2 實作~~ → 不做（見上）
- [x] ~~P3 驗收（A1–A5）~~ → 不做（A1–A3 前提不成立）
- [ ] ⬜ **operator 追認**：①關閉本立項 ②開立 §「MTP 口徑重定義」立項（0 build，純標注）
  —— **本線建議＝①是、②是**；見 `MTP_BITIDENT_CLOSEOUT_2026-09-28.md` §5／§7
- [ ] ⬜ 若 operator 仍要求追求 bit-identical ⇒ 屬 **P2-b（動 recurrent kernel）**，需另約窗口與預算，
  且須先接受「修好也負收益」的評估

> ⚠ 在 operator 追認前，**交付口徑維持 `prod-new` 的 MTP off**，本線不改任何預設值。
