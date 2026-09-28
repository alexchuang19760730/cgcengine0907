# MTP ON 驗證三件事（2026-09-28 12:16–12:45）

範圍：本線（引擎層 + `scripts/check/`）。本輪**沒有 build、`src/` 一行未改、沒動 `run_server.sh`**。
三件事：(1) 修 `--arm '!K=V'` 的靜默丟失；(2) 驗 M1/M2/M3；(3) 乾淨窗口配對重跑。
底座：`docs/MTP_ON_RC6_ROOT_CAUSE_2026-09-28.md`（根因）、`docs/MTP_ON_FIX_RESULT_2026-09-28.md`（方案 A 首跑）。

---

## §1 `!` 前綴：已修，並且立刻抓到兩個既有的靜默失效臂

### 1.1 改了什麼（`scripts/check/llama_bench_matrix.py`）

- 新增 `parse_arm_env()`：**剝掉 `!`**（語義與 `harness.py:_parse_arm` 對齊），取代 `:783` 原本的
  `dict(kv.split("=", 1) …)`。
- 新增 `arm_env_dropped()`（**fail-closed 防線**）：arm 宣告的 `KEY=VAL` 若
  ① 沒在 resolved env/scalars 裡逐字出現，**且** ② 單獨帶著它 resolve 一次也沒讓 surface 產生任何變化
  ⇒ 判「沒送達」，預設 `SystemExit` 拒跑；`CGC_ARM_ENV_STRICT=0` 可降為警告。
  為什麼要第 ② 條：`CGC_SERVER_LAYER_CAPS` 這類「入口名」在 resolved env 裡是以
  `LLAMA_EXPERT_CACHE_LAYER_CAPS` 出現的，只比名字會誤傷。
- 自測擴到 17 項：`--selftest` 全過（6 spec-armed + 3 sampling + 2 e2e + 2 parse + 4 landed）。

修復前後的硬證據（resolve-only，不碰 GPU）：

```
!CGC_GATHER_SLAB_CAP=64   → dropped=['!CGC_GATHER_SLAB_CAP=64']   （= 引擎吃到的是預設 256）
CGC_GATHER_SLAB_CAP=64    → dropped=[]
CGC_SERVER_LAYER_CAPS=40-40:16 → dropped=[]（轉成 LLAMA_EXPERT_CACHE_LAYER_CAPS 出現）
```

### 1.2 ⛔ 波及面：兩個既有結論是建立在「沒送達」的臂上

**(a) `Backup/phase_decomp/g2_smallpool`（G2 線的那個「小池」臂）實際上是 8 GiB 池。**

`Backup/phase_decomp/g2_smallpool/harness_stdout.log:8` 的臂規格第一個鍵帶 `!`：

```
--arms prod-new:!CGC_EXPERT_CACHE_BYTES=268435456;CGC_SEG_BATCH=1;CGC_B_SCHEME=1;…
```

實測（本輪）：`parse` 後 `CGC_EXPERT_CACHE_BYTES=268435456`，但 resolved 出來是
**`CGC_EXPERT_CACHE_BYTES = 8589934592`**，`dropped=['CGC_EXPERT_CACHE_BYTES=268435456']`。
⇒ 那一趟**沒有**跑在 256 MiB 小池上；它後面那些沒帶 `!` 的鍵（`CGC_SEG_BATCH` 等）是真的生效了，
所以只有「小池」這個變因是空轉。**凡是拿 g2_smallpool 當「小池對照」的推論都要重跑。**

**(b) 內建臂 `prod25-stream-noprewarm` 從來沒有關掉 prewarm。**

`CGC_NO_PREWARM` 在 `src/llama.cpp/src/llama-context.cpp:2037` 被讀，但**不在 `run_server.sh` 的
allowlist 裡**（`grep -n 'SERVER_ENV+=(' scripts/run_server.sh` 找不到）⇒ 該臂一直是有 prewarm 的。
新防線現在會拒跑這個臂。**這需要擁有者決定**（`run_server.sh` 是共用資產，本輪未動）：

- 加進 allowlist（那這個臂從此真的關掉 prewarm，行為改變）；或
- 退役這個臂（它從來沒量過自己名字宣稱的東西）。

其餘帶 `!` 的痕跡（文獻級，非本輪實跑）：`docs/PROD_NEW_TEST_CARD_2026-09-24.md:155`
（`!CGC_EXPERT_SKIP_READRAW=0`）、`docs/ONE_CLEAN_SLOT_2026-09-26.md:75`
（`!CGC_SERVER_EXPERT_CACHE_BYTES`）、`scripts/check/harness.py:53` 的教學範例
（`!CGC_WAKE_POLL_US=999`）——**這行教學是錯的**，照抄會靜默空轉。

### 1.3 順手發現（未修，回報）

`scripts/check/m123_oracle_gate.py` 對 **repo 之外的 `--ref` / `--write-ref` 路徑會崩**
（`ref.relative_to(ROOT)`，`ValueError`，兩處：`:1055` 與 `:1291`）。`--write-ref` 的崩潰發生在
複製檔案**之後**，所以參考檔其實寫出來了，但比較沒跑。本輪的繞法是把 ref 放進
`Backup/m123_oracle_gate/`（repo 內）。一行修正，但屬共用資產，未動。

---

## §2 M1/M2/M3：**「draft 只影響速度不影響 logits」不成立**

### 2.1 方法與為什麼不能直接讀 gate 的數字

工具：`scripts/check/m123_oracle_gate.py --profile prod-new`，兩側 `CGC_SERVER_TEMP=0`（greedy，
避免採樣隨機），長探针「請用繁體中文簡短介紹台灣的三個觀光景點，每點一行。」、`--probe-max-tokens 48`。

`--spec-type draft-mtp` 一開，dump 的**形狀就變了**，而 gate 是用 `(step, token_idx, ctx_type)` 當鍵的：

| | 短探针（15+27） | 長探针 |
|---|---:|---:|
| OFF（MTP=0） | 5 筆，全部 `ctx=DEF` | 50 筆，全部 `DEF` |
| ON（MTP=1） | 9 筆 = 2 `DEF` + 3 `MTP` + 1 批 `DEF(ntok=4)` | 176 筆 = **101 `DEF` + 75 `MTP`** |

所以 gate 印的 `M1 1/14`、`coverage 8%` **不是判決**——它自己也印了「keys collide while describing a
different token sequence」。同理，按 `pmax` 重新對齊（`DEF` 僅 27 個共同鍵、M1 0/27）也不能當判決：
序列一分岔，「同一個 pmax」就不再是同一個 token。

### 2.2 能當判決的東西：兩側各自可重現，但**彼此不同**

| 臂 | 答案（greedy，temp=0） |
|---|---|
| OFF | 台北101：地標建築，**擁有世界最高可運作觀景台**，夜景璀璨。／阿里山…日出及小火車聞名… |
| ON 第 1 次 | 台北101：地標建築，**兼具現代科技與傳統文化意象**。／阿里山…日出聞名…／墾丁國家公園… |
| ON 第 2 次 | **與 ON 第 1 次逐字相同**；dump 176/176 筆 `row_fnv1a64` **全部 bit-identical** |

⇒ 兩側各自是確定的，**卻是不同的輸出函數**。這不是抽樣噪音（ON 自己兩次 176/176），也不是
溫度沒生效（OFF 在 temp 0.4 / temp 0 下的輸出只差在被截斷的尾巴，前段一致）。
**M2（decision agreement）在 MTP ON vs OFF 之間是 FAIL**——不是「差一點點的數值漂移」，是第 8 個
token 就換了詞。

### 2.3 ⚠ 歸因還沒做完：是「那一整包」，不是已證的「MTP 本身」

`--spec-type draft-mtp` 同時帶進來的 env（gate 列的 config diff 共 30 項）包括：
`CGC_DRAFT_DECODE=1`、`CGC_VERIFY_DECODE=1`、`CGC_NO_PREFETCH=1`、`CGC_NO_SEQ_RM_PROBE=1`、
`CGC_PREFIX_REUSE_CKPT=1`、`CGC_WARM_NPAST=0`、`LLAMA_EXPERT_CACHE_LAYER_CAPS=40-40:256`。
其中 `CGC_PREFIX_REUSE_CKPT` 直接動 KV 狀態，是**輸出分岔的首要嫌疑**。
⇒ 現階段只能說「**開 MTP 那一包會改變輸出**」，要說「MTP 的 draft/verify 機制改變輸出」還需
逐鍵 ablation（`CGC_SERVER_PREFIX_REUSE_CKPT=0` 等）。本輪未做。

### 2.4 對結論的直接衝擊

之前把 MTP ON 的 tg 數字當成「同一份工作、花更少時間」來比較。**這個前提現在不成立**：
ON 做的不是同一份工作（輸出不同）。所以「ON 比 OFF 慢多少」這句話不只是污染問題，是**比較對象錯了**。

---

## §3 乾淨窗口配對重跑：**沒拿到乾淨窗口**

12:32 / 12:34 背靠背，同 build `e5d1c0f14`，同一權威 cell。

| 臂 | pp 中位 | tg 逐 rep | tg 中位 | hit% | misses | attribution |
|---|---:|---|---:|---:|---:|---|
| OFF（`prod-new`） | 262.30 | 11.92 / 11.70 / 11.98 | **11.92** | 96.2 | 4951 | `both` |
| ON（`40-40:16` + 兩道 draft 閘） | 258.15 | **0.39** / 7.34 / 8.77 | 7.34 | 90.8 | 8829 | `both` |

- 兩臂 `attribution.verdict='both'`：`thermal worst=HEAVY`、swap 成長 +1320 / +939 MiB
  （起跑時壓縮機是 QUIET，但機上其他 app 佔著 8–9 GB swap 存量）⇒ **數字不可定價**，只能講方向。
- 方向與上一趟配對一致（11.53 → 8.26；本趟 11.92 → 7.34）：**ON 明顯較慢**。
- **新發現：ON 的 rep1 是系統性暴走**（上一趟 0.45，本趟 0.39），不是噪音。伴隨
  misses 8829 vs 4951、capacity 25.7% vs 17.7%，且 rep3（8.77）仍在爬 ⇒ 3 reps 內 ON 尚未穩態。
  合理懷疑是 rep1 在付「draft ctx 建立 + blk.40 首次進池」的一次性成本。

---

## §4 下一步（按成本排序，都要先過環境閘門）

1. **ablation 輸出分岔**（§2.3）：ON 臂逐關 `CGC_PREFIX_REUSE_CKPT`、`CGC_NO_PREFETCH`、
   `CGC_WARM_NPAST`，找出哪一個把答案換掉。這是「MTP 能不能當純加速開關」的門檻題。
2. **`prod25-stream-noprewarm` 的處置**（§1.2b）：allowlist 補鍵 vs 退役 —— 需擁有者決定。
3. **重跑 g2_smallpool**（§1.2a）：改成不帶 `!` 的寫法，看 G2 的「小池」結論是否還站得住。
4. 乾淨窗口：機上其他 app 讓出 swap 之前，配對數字都只能當方向。
5. `m123_oracle_gate.py` 的 `relative_to` 崩潰（§1.3）：一行，待授權。
