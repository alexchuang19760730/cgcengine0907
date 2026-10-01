# 三條候選的執行規格（2026-09-27 02:2x）

> ⚠ **輸出函數標籤（2026-09-28 依 `MTP_CALIBER_REDEFINE_CHARTER_2026-09-28.md` B2 重標）**：
> 本檔引用的 **12.57／12.62 t/s 屬 MTP-on 輸出**，是**另一個輸出函數**，
> **不可與 MTP-off 互比**（含「MTP 加速 X%」「MTP-on ≥ MTP-off」「以 12.57 為基線／錨點／要打敗的數字」這類表述）。
> 根因：MTP on ⇒ `cparams.n_rs_seq > 0` ⇒ `delta-net-base.cpp:494` recurrent 走 K 槽位回滾，
> **計算份數本身不同**（`gdn` 30→60、`conv_input` 30→150）⇒ 見
> `docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md`。本檔既有數字**未改動**。

> 起因：`docs/NEXT_MILESTONES_2026-09-27.md` §2 列了三條候選，每條只有一句判據與一句成本。
> 本文把它們補到**可以照著跑**的形狀：具體開關、前置確認、判據量綱、證伪分支、成本算術。
> **跑 0 GPU、0 建置**；所有數字都有出處，沒有出處的標「推算」。
>
> 一句話結論：**軸 D 的期望值在本輪查證中需要下修**（理由見 §2.1），其餘兩條的**執行成本**
> 均可由本文直接估。

---

## 0. 三條共用的判讀前提（不重複聲明）

| 項 | 值 | 出處 |
|---|---|---|
| 護欄 | M1/M2/M3 bit-identical 是**護欄不是獎勵**；不能拿它換速度 | operator 09-26 權威表述 |
| 噪聲底 | 控制臂 `tg` 漂移 **±11.3%**（09-26 實測） | `NEXT_MILESTONES` §0 |
| 效應門檻 | 交付級 **3%**；而**任何 <12% 的收益目前判不出來** | `NEXT_MILESTONES` §0 |
| 對照臂 | **同 build**（dylib md5 一致）＋ 相鄰一對 × N，段間等 swap 回落 | `NEXT_MILESTONES` §1 |
| 「無 llama 行程」≠ 空窗 | 連續 3 次乾淨 ＋ `thermal == NOMINAL` | `NEXT_MILESTONES` §1 |
| 建置闸門 | 動 `src/` 前先查 8080 listener ＋ 別線量測行程（**檢查與動作同分支**） | `MEMORY_HYGIENE`；09-17 事故 |

⇒ **本文所有「判據」都必須落在這個前提上才算數**；偏離的任何一條，數字都不能引用。

---

## 1. 軸 D ── `E`（accept / mean len）

### 1.1 量綱與現況

```
t/s = E / step_time          （恆等式，不是經驗式）
E   = mean len = accepted / generated        【server carrier】
    = 1.70   →  0.23266 (104 accepted / 447 generated)，同一字串在 5 次獨立 launch 完全相同
E   = 2.02   →  0.34127 (129 / 378)          【09-19 那支，載入 ref_oracle_long_base2_20260919】
```

- 「`E` 是配置的函數、不是抽樣」的唯一證據：`docs/MTP_CTX_REPRODUCIBLE_2026-09-26.md` §2
  （5 次 launch 同一字串、兩臂 cache counter 逐字節相同：`requests=48317 hit=79.5% evictions=9798`）。
- ⚠ **`0.34127` / `0.23266` 是 accept *rate*，不是 mean len**；mean len 另外記載
  （2.02 / 1.70）。引用時不要混用。

### 1.1b ★★ 量綱查證（2026-09-27 02:4x）：`E = 1 + k·a`，`k = 3`（**不是幾何級數**）

`draft acceptance = a (A accepted / G generated), mean len = E` 這行把兩個數寫在同一行，
所以可以拿它反解模型。把 09-19 那批 log 裡的每一行都代一次：

| accepted / generated | `a` | 報告的 `E` | `1 + 3a` | 誤差 |
|---|---:|---:|---:|---:|
| 0 / 21 | 0.00000 | 1.00 | 1.000 | 0% |
| 4 / 12 | 0.33333 | 2.00 | 2.000 | 0% |
| 125 / 417 | 0.29976 | 1.90 | 1.899 | <0.1% |
| **129 / 378** | **0.34127** | **2.02** | **2.024** | **0.2%** |
| 142 / 402 | 0.35323 | 2.06 | 2.060 | 0% |
| 74 / 159 | 0.46541 | 2.40 | 2.396 | <0.2% |
| **104 / 447** | **0.23266** | **1.70** | **1.698** | **<0.2%** |

* 證據：`Backup/cgc_logs/llama_server_20260919_224952.log:582`（`0.34127, mean len 2.02`；
  該 log 全檔只有這一行 acceptance）＋ `MTP_CTX_REPRODUCIBLE_2026-09-26.md` §2（`104/447`）。
* ⇒ **`E = 1 + k·a` 與 `k = 3`（交付配方 `--spec-draft-n-max 3`）成立到小數第二位。**
* ⇒ ⛔ **`E ≈ 1 + r + r²` 是錯的**：那是 `k = 2` 的幾何級數。把 `r = 0.23266` 代進去得 `1.30`，
  實測 `1.70` ⇒ 幾何級數被這組資料直接排除。本文上一輪（以及 `D5_RETRO` §6 的附件）用它反解
  出 `r ≈ 0.50` / `0.85`，**是錯的模型**，以本表為準。
* ⇒ `generated` 全是 3 的倍數（`378 = 126×3`、`447 = 149×3`）⇒ 兩端的 `k` 都沒變。現狀 `k = 3`
  另有獨立證據：`docs/KEFF_CAP_2026-09-26.md` §1(b)（post-fix draft histogram max depth = 3）。
* ⚠ 同批 log 裡另有 `mean len 2.59 / 0.53061`（`78/147`）與 `2.52 / 0.51007`，但它們在
  **`llama_server_20260915_*.log`**（09-15 那一批），**不能**拿來當 09-19 的對照臂。

### 1.2 ★ 本輪查證：`0.98 → 0.44` 與 `2.02 → 1.70` **不是同一對**

`docs/D5_RETRO_2026-09-27.md` §6 斷定 `65c76b8c7`（09-25 17:16）引入的 11 行是 MTP 數值的斷點：

```c
if (params.ctx_type == LLAMA_CONTEXT_TYPE_MTP) {   // llama-context.cpp:163-168
    cparams.ctx_other = params.ctx_other;
}
```

而**那段 diff 自己的註解**寫的是（`git show 65c76b8c7 -- src/llama.cpp/src/llama-context.cpp`）：

> *…the draft ran the non-shared catch-up decode instead of reusing the target context,
> **and the accept rate collapsed (a 0.98 -> 0.44)**.*

讀法與含義（本線上一輪已糾正的**方向**，保留原文對照）：

| | 修復前 | 修復後 |
|---|---|---|
| mean len（實測） | `2.02` | **`1.70`** |
| accept rate（`65c76b8c7` msg） | `0.98` | **`0.44`** |
| accept rate（今天的實測） | — | **`0.23266`** |

⇒ 把這對數放進 §1.1b 的模型：`0.44 ⇒ E = 1 + 3×0.44 = 2.32`，與 09-21 那批的
`0.444 / mean len 2.31`（`docs/PARALLEL_AND_WORKERS_AB_2026-09-21.md:117`、
`docs/MTP_AMORTIZATION_RECHECK_2026-09-25.md:107` 的作廢數字 `E=2.31 / a=0.44`）**同一家族 ⇒ 自洽**。
⚠ **但它不是今天的數。** 今天的實測是 `0.23266`，比 commit message 記的 `0.44` 又低 1.9× ⇒
**`0.44 → 0.23266` 是另一次下降，而 `65c76b8c7` 的 commit message 只記錄了 `0.98 → 0.44` 那一筆。**
它的姊妹案件就是 `docs/KEFF_CAP_2026-09-26.md`：`65c76b8c7` 打開 `is_mem_shared` 之後，共享 KV 的
batch layout 讓 draft 第二步解在同一個 position（`X = Y`，被 M-RoPE 拒絕），`k_eff` 靜默塌到 1；
姊妹 fix（working tree，09-26 01:47 post-fix histogram max = 3）把它接回 3。

⇒ **結論：`0.98` / `0.44` 與 `2.02` / `1.70` 不同批、不同 carrier 血統，兩組都不能互相推算。**
**只有 `2.02 → 1.70` 這對是可引用的**：同 `k=3`、同 server carrier 血統、兩端各自被
`draft acceptance` 行自證（§1.1b）。

⇒ **兩組數方向一致（都是下降），不是「修好就變快」。** 我上一輪在 `D5_RETRO` §6 寫的
「修復方向是變好的（把掉到 0.44 的 accept 拉回 0.98）」是**反讀** —— 原文字序是
「*lost ctx_other → catch-up decode → the accept rate collapsed (a 0.98 -> 0.44)*」，
`0.44` 是被描述的**當前狀態**。**以本文為準，`D5_RETRO` §6 第 293 行須回改。**

⇒ **軸 D 的 `+19%`（回到 2.02）在算術上等價於 revert 那段 MTP 修復**，而那段修復的存在理由
是「MTP draft context 靜默丟失 `ctx_other` ⇒ `is_mem_shared=false`」。
⇒ **它與本線護欄直接衝突**：要拿到 +19%，得先讓 M1/M2/M3 受損。依「護欄不是獎勵」，
**軸 D 不能這樣做**。

**⇒ 軸 D 的前置門不是「MTP on 能不能跑」，而是「有沒有一條不 revert 就能把 `E` 拉回去的路」。**
若 `E` 的損失全部來自「共享 KV 後 draft 注意力看到的是 **target 的 K/V 而非 draft layer 自己的**
（`MTP_CTX_REPRODUCIBLE` §4，約 1 logit unit 的偏移）」，那這是**正確性債務的計價**，不是可優化項。
**但這一點本輪沒量** —— 見 §4「未查」。

### 1.2b ⚠ 這對數字怎麼寫才不會把槓桿的價格講錯

| 寫法 | 對不對 | 說明 |
|---|---|---|
| 「mean len `1.70 → 2.02` ＝ **+18.8%**」 | ✅ | 同 `k=3`、同 carrier 血統，且兩端都被 `draft acceptance` 行自證（§1.1b）。**這是軸 D 唯一可引用的數字。** |
| 「把 accept 從 `0.233` 拉回 `0.341` ⇒ +19% t/s」 | ❌ | accept 要 **+46.6%** 才換得到 +18.8% 的 `t/s`。這個寫法把槓桿的價格講低 2.5×。 |
| 「把 accept 從 `0.44` 拉回 `0.98`」 | ❌ | 這兩個數不屬於今天的 cell（見 §1.2），也不能與 mean len 混用。 |
| 「`+19%` 就是軸 D 的收益」 | ⚠ | 見下。 |

**最後一層：`+19%` 是上界，不是預期值。**
`docs/ACCEPT_LEVERS_AND_DIRTY_BOX_PAIR_2026-09-26.md` §2 實測：同一 `k=3` 下把 accept 從
`51.49%` 抬到 `57.94%`（+12.4%），`mean_len` 2.59 → 2.72（+5%），而 `t/s` **−0.91（−7%）** ——
**多買到的 token 付不起它帶來的 `step` 成長**（與 G6 同一個機制）。
⇒ 要把 `E` 拉回去，**必須同時證明 `step` 不漲**；這正是 §1.3 (P4) 判準
（`|E_rel − t/s_rel| < 3%`）存在的理由，而不是一個形式主義的門檻。

### 1.3 執行規格（若 §1.2 的門判為「願意做」，才走這一步）

| 步驟 | 具體動作 | 判準 |
|---|---|---|
| **(P0) 開關真相** | `CGC_SERVER_MTP=1` 走 prod25-stream 血統（MTP on）；prod-new **默認 `0`** | `run_server.sh:335-336` 原文：「`--arms "prod-new:CGC_SERVER_MTP=1"` 即可，不需新 profile」 |
| **(P1) 窗口確認** | `CGC_SERVER_PROFILE=prod-new CGC_SERVER_MTP=1`，等 `/health` 與一次 `/v1/completions` | 返回非 error。⚠ 09-26 11:29 那次四臂全掛是 **ρ prefetch 缺 prefill 閘**（已修，見 §2.2）；MTP on 的 OOM 記錄本文未定位 ⇒ **「今天 OOM 三次」這個前提本輪不複核** |
| **(P2) 對照臂** | `off`/`on` 相鄰一對 × N（N ≥ 2 段），段間等 swap 回落，全程 `thermal == NOMINAL` | 同 build（libllama md5 相同） |
| **(P3) 讀數** | 每個臂記：`mean_len`、`t/s`、step 四桶（`union`/`gap`/`busy` 為步內量測） | 見下 |
| **(P4) 判據** | `E_rel` 與 `t/s_rel` 之差的絕對值 **< 3%**（傳遞誤差） | 若 `t/s_rel > 3%` 而 `E_rel ≈ 0` ⇒ **瓶頸不在 accept**；若 `E_rel > 3%` 而 `t/s_rel ≈ 0` ⇒ **違反 `t/s = E/step_time` ⇒ 口徑錯，整輪作廢** |
| **(P5) 防線** | 每臂 post-run 跑一次 D5 默認臂 | M1/M2/M3 9/9；否則該臂不可引用 |

**成本算術**：每臂 = 一次 13 GB 載入（約 60~99 s，見 `MTP_CTX_REPRODUCIBLE` §5）+ 長探針。
`4 臂`（2 段 × 2 臂）≈ **13~19 分鐘**，不含 D5。

### 1.4 ★★ 軸 D 在 MTP off 下**除名**（2026-09-27，operator：「你先處理 mtp off 即可了」）

這一条改变的是**候選表的結構**，不是排序：

| | MTP on | **MTP off（= 交付口徑）** |
|---|---|---|
| `--spec-type` | `draft-mtp` | **不傳**（`arm_spec_flag` 的 `base` 分支，`run_server.sh:1345` 同形） |
| draft 是否存在 | 有 | **無** |
| `draft acceptance = a` | 有 | **不存在**（沒有分母） |
| `mean len = E` | 有 | **不存在** |
| ⇒ **軸 D 的因變量** | 存在 | **不存在** |

- 交付口徑 = MTP off，有兩處權威：`PROD_NEW_TEST_CARD` §`CGC_SERVER_MTP | 0`（decode 支柱，
  13-14 t/s）；`MTP_AMORTIZATION_RECHECK_2026-09-25` §1「交付口徑 MTP off = **11.03~12.20**」。
- ⚠ **軸 D 不是「期望值下修」，是「在 MTP off 下不可測」**：它的因变量（`E`）只在 MTP on 上
  存在。所以 §1.1~§1.2b 那一整套量綱查證（含 `+18.8%` 那個可引用的數字）**全部落在 MTP on 那一側**，
  在交付 cell 上**沒有落地面**。
- ⇒ **軸 D 要復活的唯一條件是把 MTP on 變成交付口徑**。那是**產品決策**（要不要為了 throughput
  接受 draft 路徑），不是本線的優化槓桿 —— 而且本線的目標函數要求 M1/M2/M3 bit-identical，
  而 §1.2 已證明「回到 2.02」等價於 revert `65c76b8c7`（一個**為了修 MTP 正確性**的修復）。
  **⇒ 軸 D 本期除名，不排任何實驗。**
- 定稿口徑（避免下次再被撿起來當成活槓桿）：**「軸 D 只在 MTP on 上存在；交付 cell 上它不是
  一個『待優化的槓桿』，而是一個『被正確性債務換掉的東西』。要它回來，得先證明付出去的那個正確性
  債務可以不清償。」**

### 1.5 `E = 1 + a·k_eff` 的權威出處（比 §1.1b 的反解更早存在）

§1.1b 是我**反解**出來的（`1+3a` 誤差 ≤0.6%，並用它排除幾何級數）。本輪找到現成定義式：
`MTP_AMORTIZATION_RECHECK_2026-09-25` §2 **F11**：

> **`E = 1 + a·k_eff`** —— 等級 `D`，理由：`a` 的**定義式**（每輪接受數 ÷ k_eff），不是經驗規律

⇒ §1.1b 的 `k=3` 是這個定義式在 `k_eff=3` 時的特例，**不是新發現**；`1+r+r²` 則是「`k=2` 的
幾何級數」，被同一批資料排除。引用時用 **F11**，並註明 `k_eff` 要實測（`KEFF_CAP_2026-09-26.md`：
`k_eff` 曾**靜默塌到 1** ⇒ 同一個 `a` 下 `E` 會差一倍）。

### 1.6 ★★ 量測入口口徑（2026-09-27 operator 指令）：**只用唯一對外門**

> **規則**：本線的任何 t/s 讀數，只從這兩條路出來 ——
> 通用測量 = `harness.py bench --arm "prod-new"`，commit 前 = `commit_bench.py`。
> **`llama_bench_matrix.py`／`prod_matrix.py` 是 internal 驅動**：直跑它們即使跑通，
> 未帶完整 §2.5 口徑標注的數字＝**「口徑不明」**，不可引用。

權威出處兩處，互相印證：

| 出處 | 原文 |
|---|---|
| `PROD_NEW_TEST_CARD_2026-09-24.md` §7 | 「**唯一對外門（2026-09-25 裁定）**：通用測量走 `harness.py bench`、commit 前走 `commit_bench.py`。`llama_bench_matrix.py`／`prod_matrix.py` 是 **internal** 驅動 …… 即使跑通，未帶完整 §2.5 口徑標注的數字＝**「口徑不明」**，不可寫進 commit 標題或跨時間比較。」 |
| `prod_matrix.py:3-5` | 「⚠ INTERNAL (2026-09-25 ruling): despite the original wording below, this is NOT the external entry point. Use `harness.py bench` / `commit_bench.py` …… do not quote them in commits.」 |

**這條的直接後果（本輪）**：`scripts/check/rho_fill_ab.sh`（含本輪新加的 `MTP_OFF`／`CELL` 對齊）
**自己拼 llama-bench 命令**，屬於「直跑 internal 驅動」的一類 ⇒ **它量到的 `t/s` 依舊是口徑不明，
不能對外引用**。它仍可作為**配對篩選工具**（同一批次內的相對比值有語意），但
**每個絕對值都必須再用 `harness.py bench`／`commit_bench.py` 重跑一遍才可引用。**

⚠ 另有兩處「看似同一件事、其實不是」的命名陷阱：

- `agent_harness/engine_loop/wrappers/bench.sh` ≠ `scripts/check/harness.py bench`。
  前者是 **wrapper 入口**（只有 `--list / --self-test / --dry-run / <script> [args...]` 四種用法，
  硬前置 `engine_loop/build.json`，缺了就 exit 1），**不是**測試書說的對外門；
  後者才是。且 wrapper 的入參是**相對 `scripts/check/` 的檔名**（傳 `scripts/check/commit_bench.py`
  會被接成 `scripts/check/scripts/check/...` 而報「不在這一類」）。
- `wrappers/bench.sh` 需要 `agent_harness/engine_loop/build.json`（**當前不存在**，
  它叫你先跑 `runners/rebuild.sh`）；`harness.py bench` 不依賴它 ⇒ **量測走後者、別碰前者**。

---

## 2. 軸 R ── ρ 的淨增益

### 2.1 開關真相（本輪查證，**這是最大的陷阱**）

`scripts/run_server.sh:1702-1717` 的原話：

> *`CGC_RHO_PROBE=1` builds the per-layer shadow router …
> **WARNING: it costs a SYNCHRONOUS READBACK per layer** (`cgc_rho_capture`, …
> `n_expert*n_tokens` floats × 40 layers/step) and is documented as a **PROBE --
> **never quote throughput from an arm that has it on alone**.
> `CGC_RHO_FILL=1` turns the prediction into a real non-blocking batch prefetch
> (`cgc_rho_prefetch`). **Inert without PROBE TODAY**, because the prediction is
> computed on the HOST from the captured logits.*

⇒ **兩條硬結論**：
1. **單開 `CGC_RHO_FILL` 是惰性的** ⇒ 這樣的 ABBA 必然測出 `Δt/s ≈ 0`，
   而它會**看起來像「ρ 無效」**，其實是臂根本沒生效。**這類陷阱本專案已重複出現三次**
   （`CGC_MISS_MASK`/`CGC_RHO_*`/`CGC_SEG_BATCH` 都是「引擎讀了，launcher 沒轉發」）。
2. **`CGC_RHO_PROBE` 自帶同步 readback** ⇒ **PROBE-only 臂的 t/s 不可引用**。
   ⇒ 軸 R 若要量 t/s，必須是 `PROBE+FILL` vs `PROBE`（把 PROBE 當常數背景），
   而不是 `FILL` vs `OFF`。**`+14.2%` / `+4.7%` 這兩個數就是從「只有 insert」的臂推的**，
   能不能平移到「PROBE 常駐」的臂，**未知**。

### 2.1b ★★ 為什麼「ρ 在交付 cell 的淨增益」從來沒量過（2026-09-27 查到**機制原因**）

不是「量過是 0」，是**歷史 A/B 兩臂都不在交付 cell 上**。看 `scripts/check/rho_fill_ab.sh` 的
臂定義（改之前的狀態）：

| 臂 | `arm_spec` 返回 | 實際口径 |
|---|---|---|
| `base` | `prod-new` | **MTP off** |
| 所有 ρ 系列（`rho`/`probe`/`rho-q*`/`rholeg`） | `prod-new:CGC_SERVER_MTP=1` | **MTP on** |

⇒ **`rho` 臂落進 `arm_spec` 的 `*` 分支**（那是為了 MTP on 的 ρ 設的默認分支）。
⇒ **`+14.2%`（模型上界）與 `+4.7%（MAXQ 內部調參差）`這兩個數的原始樣本，全部是 MTP on 的。**
⇒ 而交付口徑是 MTP off ⇒ **「ρ 在交付 cell 的淨增益」這件事，本專案從來沒有量過。**

**⇒ 本線已加 `MTP_OFF=1` 開關**（`rho_fill_ab.sh`，2026-09-27，+18 行，**未設時行為完全不變**）：

| 位置 | 改了什麼 | 為什麼兩處都要改 |
|---|---|---|
| `arm_spec` | 全臂返回 `prod-new` | 切 profile |
| `arm_spec_flag` | 全臂返回空（不傳 `--spec-type`） | **只改 `arm_spec` 會讓 llama-bench 替我們把 MTP 打開 —— 一個只看一半的開關比沒有這個開關更危險** |

**生效性已查證（避免 ABBA 測出 `Δ≈0` 的假陰性，這是本專案第三次同型陷阱）**：

| 閘 | 位置 | MTP off 下的行為 |
|---|---|---|
| `CGC_RHO_FILL` 存在性閘 | `llama-context.cpp:4444` | `getenv(...) != nullptr` ⇒ 設定即活 |
| draft 閘 `ctx_type != DEFAULT` | `:4451` | 擋的是 **MTP draft ctx**；MTP off 下主幹 `ctx_type = DEFAULT` ⇒ **不擋，照常工作** |
| 相位閘 `cgc_is_decode_graph` | `:4559` | decode 步照常通過 ⇒ `cgc_rho_capture` + `cgc_rho_prefetch` 執行 |

⇒ **ρ 在 MTP off 下是真活開關，不是惰性。** 若 ABBA 量出 `Δ≈0`，那是效應為零，不是開關沒生效。

**首跑被擋（2026-09-27 02:4x，未得到任何 t/s 數字）**，兩道閘都過了：

1. `BUDGET_GATE=strict`（預設）⇒ exit 2：`pool 8 GiB + load_mode=none` = **靜態超訂 4838 MiB**
   （`21222 > 16384`）。$\Rightarrow$ 唯一能跑這組配置的通道是 `BUDGET_GATE=warn`，代價是樣本**帶
   `CGC_BUDGET_OVERSUBSCRIBED=1` 標記、不可當乾淨基線**（但 **base vs rho 是同一批次、同一
   budget 狀態 ⇒ 配對比較仍然有效**，只是絕對值不可引用）。
2. thermal gate 更硬：**`swap = 4965 MiB` > `MAX_SWAP_MIB = 2048` ⇒ `REFUSE`**。
   09-24 的對照點是 `swap 5301 MiB ⇒ base 12.57 → 4.62`（池被換出、hit% 68.7→57.2）。

⚠ **我在這兩條下面原寫「阻塞是物理資源不是設定 ⇒ 要等 swap 落到 2048 以下」，這句被 02:5x 的
實測判定為過強**：macOS 在 **62 秒內把 swap 自行回收到 2584**，之後 `BUDGET_GATE=warn` +
`MAX_SWAP_MIB=4096` 就讓臂跑完了（見下面 §2.1d）。真正的 blocker 是 **cell 口徑**（§2.1c），
不是 swap。**保留原文對照，不抹掉** —— 誤判的方向是「把可以等到的資源當成不可等」。

### 2.1c ★★★ 為什麼這支腳本**從建成以來就跑不通**（2026-09-27 實測）：cell 定義與權威 cell 四項不符

探路第一槍就拿到 `t/s=NA`（02:48），查 `/tmp/rho_ab_024812/base_r1/driver.log`：

```
⛔ cell 口徑與測試卡權威 block 不一致 — 拒跑（fail-closed）：
     - batch:    實際 512  ≠ 權威 5632
     - ubatch:   實際 512  ≠ 權威 5632
     - prompt:   實際 0    ≠ 權威 2048
     - reps:     實際 1    ≠ 權威 3
```

`rho_fill_ab.sh` 的 `CELL` 是 2026-09-23 寫的（`-p 0 -b 512 --ctx-size 4096`），而測試卡
§2.5 的 machine-readable CELL 是之後定的。**⇒ 這支腳本 2026-09-23 寫成以來就沒有跑通過**
（歷史上那兩個 ρ 數字是更早別的路徑的產物，**不是這支腳本的產物** —— 引用時要分清楚）。
⇒ 「ρ 在交付 cell 從來沒量過」背後其實有**兩道**機制原因，第二道就是這一道。

**修法（已改）**：`CELL` 對齊 §2.5（`--prompt 2048 --batch 5632 --ubatch 5632 --ctx-size 0`，
`REPS` 預設 1→3），並在 `REPS` 的**源頭**改（見下）。改完 `cell contract` 校驗通過
（嚴格 14 項一致，僅一項不阻塞的 `fixed_fill_seed: 1 → None`）。

**但對齊之後要立刻講清兩件事**：

1. **與歷史數字不可比**：`batch` 差 11×、`prompt` 0 vs 2048 ⇒ 歷史 ρ 數字與本次**不能同一張表比較**。
   本次是**第一次**在權威 cell 上量的 ρ A/B。
2. **decode 軸仍然拿得到**：`commit_bench.py` 本身就是這個形狀（2048 prefill + 128 decode，
   decode t/s 取自同一 rep 的 tg 軸）。差別只是每 rep 多付一次 prefill，且 batch/ubatch 變化
   主要吃 compute buffer 的駐留（decode 步每步只處理 `ntok` 個 token，對 batch 應近乎中性 ——
   **這是預期，不是已驗證**；若 decode t/s 因此偏移 > 噪聲底，就是中性假設錯了）。

**踩到的第二個坑（`REPS` 覆寫次序）**：我先在第 45 行加了 `REPS="${REPS:-3}"`，但第 24 行
更早的 `REPS="${REPS:-1}"` 已經把 `REPS` 設成 `"1"` ⇒ `${REPS:-3}` 取既有值 `1` ⇒
命令帶著 **`騙人的 `-r 1`** 去跑、然後被 cell_contract 擋下 —— 而擋下它的不是我以為改好的那一行。
⇒ 守則：`${VAR:-default}` **只在變數尚未被賦值時生效**，上游一旦賦值，下游的「改預設」就是無效的；
要改就改**源頭**，並在下游留註解說「這裡不要再賦值」。

### 2.1d ★★★ `base` 臂首跑定案（2026-09-27 02:5x 實測）—— **`7.49` 不是回歸，是不同口徑**

`MTP_OFF=1 ROUNDS=1 ARMS="base" BUDGET_GATE=warn MAX_SWAP_MIB=4096` ⇒ 拿到第一個 MTP off 的
交付 cell 讀數（`TAG=025009`，`/tmp/rho_ab_025009/base_r1/`）：

| shape | t/s | ± | n_gen | hit% | 環境 |
|---|---:|---:|---:|---:|---|
| `pp p=2048 n=0 d=512` | **198.64** | 26.18 | — | 96.3 | thermal **HEAVY**(163/211)、swap_growth +4095 MiB、launch NOMINAL |
| `tg p=0 n=64 d=512` | **7.49** | 0.60 | 64 | 96.3 | 同上 |

**第一件事就糾掉我自己上一個小時的誤讀**：我在對話裡說「7.49 ≠ 錨點 12.57 ⇒ 有問題」——
**錯的讀法，而且錯在兩處，每處都能單獨致命**：

| | 我以為 | 其實（權威出處） |
|---|---|---|
| `12.57` 是 MTP 哪個狀態 | 「MTP off 的錨」 | **`--spec-type draft-mtp` ⇒ 含 MTP ON**，且 `MTP_ON_OPTIMIZATION_PLAN:44` 明寫「它不是 OFF 參考」 |
| `12.57` 的 shape | 與我們同 | **`--prompt 0`（純 decode）**；我們是 `--prompt 2048`（prefill+decode 混合） |

⇒ **`7.49` 與 `12.57` 在「MTP 狀態」與「shape」兩個維度上都不同 ⇒ 不存在回流，也不能互相除。**
一處不同的百分比（例如「ρ 帶來 +14%」）拿去和 `12.57` 除，得到的是一個**沒有語意的比值**。

**第二件事：`n_gen=64` 不是口徑 bug，是 `--warm-skip 64` 的設計行為。**
`PRODUCTION_PROFILE_2026-09-20.md:123`：「`--warm-skip 64` ⇒ 報告的 `n_gen` 是 **64**」；
同一份檔記載 `-n 128`（無 warm-skip）讀 **7.96**、`-n 512` 讀 **10.22** —— **同一個引擎三個數**。
⇒ 引用 tg 必須連 `--gen` 與 `warm_skip` 一起引用，只講 t/s 的數字是無根的。

**第三件事：base 臂本體是健康的**（不在健康範圍內才算回歸）：

- 歷史 MTP-off OFF 臂（`MTP_AMORTIZATION_RECHECK` §1，`prod-new -p 0 -n 128 -d 512` ABBA 3 輪）：
  **8.315 / 8.552 / 7.102**（中位 8.315）。今天的 `7.49 ± 0.60` **落在該區間內**。
- ⚠ 但那批是 `-p 0`，我們是 `-p 2048` ⇒ **嚴格說不同 shape**；`7.49 vs 7.96`（同 `warm-skip`、
  一個 `-p 0` 一個 `-p 2048`）的 −5.9% 歸因給 warm-prefill，**未分離**。⇒ **`7.49` 只能當本批次
  的 paired 分母，不能當「MTP off 的絕對錨」**。
- ⚠ 本臂環境標記為 `thermal=HEAVY`（`hist {'NOMINAL':26,'MODERATE':22,'HEAVY':163}`）、
  `swap_growth=+4095 MiB`、`min_free=14.1 MiB` ⇒ **絕對值不可引用**；它成立的理由是
  「同批次、同一污染狀態下 base vs rho 的**輪內比值**」。

⇒ **「base 臂在 02:51 能跑」是運氣，不是條件** —— 它啟動前 `swap=2568`（低於閘門），
過程中 `swap` 一路漲到 `worst=8297 MiB`，只是**恰好沒撞到池被換出**，所以 `hit%` 保住 96.3。
⇒ **`base` 這個樣本不可複現，也不構成「該機制的基線」**。

**ABBA 立即被同一道閘挡回來（`TAG=0935`，三臂全部 `REFUSED`）**：

```
r1  base    REFUSED: REFUSE swap 4392 MiB > 4096 (pool 被換出，hit% 崩)
r1  rho     REFUSED: REFUSE swap 4392 MiB > 4096 (pool 被換出，hit% 崩)
r1  base    REFUSED: REFUSE swap 4392 MiB > 4096 (pool 被換出，hit% 崩)
```

⇒ **本輪「軸 R（MTP off）」的狀態從『待跑』變成『有闸门、且當下無窗口』**。
裝填：`swap` 4264 → 4096 就能跑；97 分鐘內從 `4965 → 4264`，方向對但速率屬於「等不到」的量級。
**不能靠 `purge` 快進**：鏈路上註明「swap 只累積不回收」，已換出的頁不會自己讀回來。

⚠ **為何不能抬 `MAX_SWAP_MIB` 硬跑**：ρ 的增益機制**本身就是「提前把專家 fill 進池、避免 miss
卡在 decode 中間」** —— 在池已被換出的狀態下跑，兩臂的 `hit%` 都崩，而 ρ 的效應對 `hit%`
是一階依賴 ⇒ **配對比值也會失真，只是失真方向未知**。 ⇒ 在 `swap < 4096` 之前，這個 A/B
**跑不出可用答案**，不是「數字乾淨度打折」而已。

### 2.1e ★★★ 用**唯一對外門**取到的 prod-new 生產級基準（2026-09-27 09:47）

指令（測試卡 §7 的唯一對外門，§1.6 的規則）：

```sh
python3 scripts/check/harness.py bench --arm "prod-new" \
    --json /tmp/harness_prodnew_094531.json
```

產物 `/tmp/harness_prodnew_094531.json`，`contract.ok = true`、`base_check.pass = true`、
`engine_build = 825ec07d5`（build_number 627）、`spec_type = None` ⇒ **MTP off（交付口徑）**。

```
prod-new  pp  p=2048 n=0   d=512 ->  230.35 ± 31.12 t/s   hit% 96.4  NOMINAL
prod-new  tg  p=0    n=64  d=512 ->   10.84 ±  0.39 t/s   hit% 96.4  NOMINAL
          cache: hit 96.4%  misses 4703  cap% 15.7
```

| 維度 | 值 |
|---|---|
| `-b / -ub` | `5632 / 5632` |
| `--load-mode` / `-ngl` / `-expert-cache` | `none` / `99` / `8589934592`（8 GiB） |
| `--fixed-fill-seed` / `--warm-skip` | `1` / `64`（報告的 `n_gen`=64） |
| swap 三支柱（`base_check.swap_arms`） | `CGC_EXPERT_SKIP_READRAW=1`、`CGC_POOL_MADVISE=2`、`CGC_B_SCHEME=1` 全武裝 |
| thermal | launch NOMINAL、worst NOMINAL（164 取樣全 NOMINAL） |

**⇒ 這一行回答「decode 現在這麼慢，不是有 12+ 嗎」：`prod-new` 生產級 decode 是
`10.84 t/s`（platform `11.03`），不是 12+。** 差額不是回歸：`12.57` 是 **MTP ON + 老 cell**
（`-b 512 / -p 0 / --ctx-size 4096`），`10.84` 是 **MTP off + 09-24 §2.5 權威 cell**
（`-b 5632 / -p 2048`）。`commit_bench` 的閘門是 `decode >= 10` ⇒ **這輪只高 8.4%，貼著線過**。

⚠ **兩個必須一起講的警號**（它們決定這輪能不能被當成「好的」）：

1. **`attribution.verdict = "swap"`** —— `swap_growth = +2406 MiB`、`max_swap = 8382 MiB`
   （ launch 4160 → end 6566）。thermal 全程 NOMINAL，但**記憶體這一維沒有乾淨**：
   ⇒ **這輪是「在最髒的記憶體狀態下恰好沒有炸掉」**，`10.84` 是**上界**不是中位表现。
2. **與 §2.1d 的 `7.49` 差 44.7%，且方向與 swap 的直覺相反**（swap 更髒反而更快）
   ⇒ 差的那一截**歸因未分離**。已知至少兩個候選，都不足以解釋：
   · `7.49` 那趟（`rho_fill_ab.sh`）**沒帶 `--fixed-fill-seed`** ⇒ 每 rep 換一條填充流＝
     cold/steady 混樣（受害者通常偏低，但方向不保證）；
   · `7.49` 那趟啟動前 `swap = 2568`（低）、本趟 `swap = 4160`（高）⇒ swap 方向也反著。
   ⇒ **在這一格被分離之前，`7.49` 與 `10.84` 只能並列，不能互除、不能說「+44.7%」。**

### 2.2 prefill 閘的現況

`cgc_rho_prefetch()`（`llama-context.cpp:4443-4449`）：

```c
static const bool on = getenv("CGC_RHO_FILL") != nullptr;
if (!on) return;
if (cparams.ctx_type != LLAMA_CONTEXT_TYPE_DEFAULT) return;   // 擋 MTP draft
```

- 09-26 修的就是「`per_layer=255`（≈整層）」⇒ 改為按 `ctx_type` 分流的容量防線。
- 現樹用 `cparams.ctx_type`，**不是** 09-26 文件寫的 `cgc_is_decode_graph`（`cgc_is_decode_graph`
  見 `llama-cgc-phase.h:141`，用在 `:1980` / `:2096` / `llama-graph.cpp:2022,2213`）。
  兩個 predicates 語義不同（decode 寬度 vs context 類型）⇒ **若要統一，得先說清用哪一個**。

### 2.3 執行規格

| 步驟 | 具體動作 | 判準 |
|---|---|---|
| **(R0) 生效性檢查** | 先跑一次 `CGC_RHO_PROBE=1`（不帶 FILL），看 log 是否出現 shadow router 的蹤跡 | **沒有 ⇒ 開關沒生效，整輪作廢**（不要換來換去浪费載入次數） |
| **(R1) 臂定義** | A = `PROBE=1 FILL=1`，B = `PROBE=1`（FILL off）。相鄰一對 × N | 同 build |
| **(R2) 讀數** | `t/s`、`cb`（expert cache 填池 IO，**線 I 的口徑**，不是 command buffer）、`hit%`/`cap%` | 09-25 真曲線：`pool 8GiB → hit 92.8% cap 3.8%`，6GiB → 89%/15%，4GiB → 85.1%/32.1% |
| **(R3) 判據** | 見下 | |
| **(R4) 防線** | 每臂 D5 默認臂 9/9 | 否則數字不可引用 |

**判據（三重，缺一不可）**：

1. **`t/s` 改善 > 噪聲底**（現 ±11.3%）。⇒ **軸 R 排 M1（噪聲底 <5%）之後**，不是現在。
2. **`cb` 下降**，且 **`gap`/`union` 不上升**。
   理由：`ρ` 若真的把 fill 藏進 GPU work 底下，`cb` 會降而 `union` 不變；
   **若 `cb` 降但 `gap` 升，那是把 CPU 時間推給了 GPU 空窗 —— 目標函數要的正是「cb 消失」，
   而 `gap` 是最不該漲的那一個**。
3. **`hit%` 不降**（否則 prefetch 排錯了專家，等價於 evict 熱專家）。

**成本**：低（不需新儀器），但 **每臂含 PROBE 的同步 readback** ⇒ 每臂 t/s **不可引用**，
只能引用「有 readback 的對照差」。若不接受 ⇒ 軸 R 本期**不能做**，直接降到維護性工作之後。

---

## 3. 2.3 hook 做便宜（barrier）

### 3.1 這個 8% 是什麼性質（本輪查證）

- 出處不是量測，是 **09-23 從 `cb 23.8` 用 F1/F5 兩個常數反推的固定項**：
  「`每層 barrier ≈ 12.9 ms`（`0.46 ms/層 × 約 28 層有 miss`，**只要該層有 ≥1 miss 就付**）
  ＋ 約 16 次 fill × 0.695 ms ≈ 11 ms」（`2026-09-23.md:580`、同見 `2026-09-24.md:146,212,322,337`）。
- **`12.9 / 157.8 ≈ 8.2%`**：分母 `157.8` 是 09-23 那次的 step 牆鐘
  （`union+gap = 157.8 vs total 161.0`，差 2% ⇒ 可對賬）。
- ⚠ **`barrier` 這個詞在本 tree 的 `src/` 裡不存在**（`grep -ri barrier src/llama.cpp/src/*.cpp`
  除了 `atomic`/`std::` 過濾外**零命中**）⇒ 它是推算量，不是可插樁的對象。

### 3.2 ★ 與 `n_main` 的反例風險（必須先講）

`n_main=32`（09-26）的量測結論是 **`union −22.6%`／`busy −48.4%` 過檻，而 `gap`、`tg` 在噪聲底內**，
判定為「消掉的是並行重疊，不是空窗」⇒ **`n_main` 作為交付槓桿判死**（總時間守恆）。
⇒ **barrier 的 12.9 ms 同樣可能只是「換個桶」**：從 `cb` 挪到 `gap` 或 `submit`。
⇒ **只看 `tpot` 會得到假的收益。** 這正是本條判據要三重的原因。

### 3.3 執行規格

| 步驟 | 具體動作 | 判準 |
|---|---|---|
| **(H0) 先有儀器** | 現況**沒有**能直接量 barrier 的插樁（3.1）⇒ 第一步不是改代碼，是**先把那 12.9 變成可量** | 例如按 `CGC_MISS_MASK_COST` 的格式，加一個「每層有 ≥1 miss 就記一次」的計數器 |
| **(H1) 基線** | 改代碼前先取得「`miss 層數`」的分佈（不是總和） | 分佈決定 12.9 是不是真的（28 層這個假設） |
| **(H2) 優化** | 降低「有 miss 的層」的 barrier 代價 | 例如合併相鄰層的同步點／去掉單層路徑 |
| **(H3) 判據（三重）** | ① `miss 層數` 的 barrier 總和下降；② **`tpot` 跟著降**；③ `gap`/`union`/`cb` 不上升 | 三條全中才算數；缺 ② ⇒ 假的；缺 ③ ⇒ 換桶 |
| **(H4) 防線** | D5 默認臂 9/9 ⇒ 動了 `src/` 就必須跑 | 同上 |

**成本**：低（常數優化），**但 `H0` 本身的成本是「先做一個插樁」**，不是零。
**排名**：`NEXT_MILESTONES` §2.3 說「若軸 E 做得出來，這個應該排在後面」——
本輪建議改為「**排在 §3.1 的性質查清之後再決定**」，因為現在連分母都沒量過。

---

## 4. 排序（本輪修正）

| 順序 | 項 | 修正理由 |
|---|---|---|
| ~~1~~ | ~~軸 D 的前置門~~ → **除名** | 2026-09-27 operator 裁定「先處理 mtp off」。查完（§1.4）：**軸 D 的因變量（`E`）只在 MTP on 上存在，而交付口徑是 MTP off ⇒ 它不是「期望值下修」，是在交付 cell 上不可測**。加上 §1.2 已證「回到 2.02」等價於 revert `65c76b8c7`（一個為了修 MTP 正確性的修復）⇒ **要它回來是產品決策，不是本線槓桿。本期不排任何實驗。** |
| **1** | **M1（噪聲底 <5%）** | 剩下兩條（軸 R、2.3）**共同的**判讀前提。不先做，兩者都判不出來（`±11.3% > 效應`） |
| **2** | **軸 R（MTP off）** | 客戶端已就緒：`MTP_OFF=1` 開關已加、生效性已查證（§2.1b）⇒ **唯一阻塞是 `swap = 4965 MiB` 的硬闸门**，不是設定也不是知之為不知。**swap 一旦回落到 2048 以下，這一格就能直接跑，不需再任何準備。** ⚠ 且必須跑 `BUDGET_GATE=warn`（超訂 4838 MiB），樣本帶標記、絕對值不可引用 |
| **3** | 2.3 hook/barrier | 8% 是推算不是量測（§3.1）；`n_main` 已示範「換桶」風險。排在軸 R 之後，因為它倆共用同一套 M1 |
| — | 軸 D 的 GPU 實驗 | **不排**（見上）。若要復活，唯一條件是把 MTP on 變成交付口徑 |

**未查（明確登記）**：
1. ~~`0.98 → 0.44` 與 `2.02 → 1.70` 是否同口徑~~ → **已查（§1.1b／§1.2b）**。答案：
   **`2.02 / 1.70` 同口徑且可被 `draft acceptance` 行自證（`E = 1 + 3a`）；`0.98 / 0.44` 不是這套
   血統（今天的實測是 `0.23266`，比 `0.44` 又低 1.9× ⇒ 中間還有一次未記錄的下降）。**
   ⇒ 「`+19%` 是 mean len 之比」成立；「把 accept 拉回 `0.341` ⇒ +19%」不成立（那是 +46.6%）。
   ⚠ 該數字現在只是 MTP on 的史料，仍在文內保留，但**不再是候選槓桿**。
2. ~~MTP on 的 OOM 記錄~~ → **不再需要定位**（軸 D 在 MTP off 下除名，MTP on 的實驗一律不排）。
   若日後要把 MTP on 變成交付口徑，這一條會重新變成 P0。
3. ~~`ctx_type` 判斷與 `cgc_is_decode_graph` 的關係~~ → **已查（§2.2／§2.1b）**，答案是不必統一：
   兩個閘管的**維度不同** —— `:4451` 按 **context 類型**（擋 MTP draft ctx，免它去污染主幹 slot 選擇）；
   `:4559` 按 **decode 寬度**（擋 prefill，因 prefill 的 top-8 聯集約等於全 256 專家＝整層讀）。
   `:4559` 已有長注釋說明它為何必須用 `cgc_is_decode_graph`（`:6262` 的 `cgc_probe_is_decode` 用同一個
   predicate；再多一種「decode」的定義就是第五種，而兩種後果都是靜默的）。

**不宣稱**：本文不主張軸 D 可翻案，也不主張 `+19%` 可回收或不可回收 —— 那是 §4.1 之後的事。
本文只把「`+19%` 不是免費的」這件事放到它該在的位置。

---

## 5. 引用

- 三條候選原文：`docs/NEXT_MILESTONES_2026-09-27.md` §2
- `E` 的可量性證明：`docs/MTP_CTX_REPRODUCIBLE_2026-09-26.md` §2（機制解釋 §4、不自稱 §5）
- MTP 斷點：`docs/D5_RETRO_2026-09-27.md` §6（⚠ 本文 §1.2 糾正其第 293 行）
- ρ 開關：`scripts/run_server.sh:1702-1730`
- `cgc_rho_prefetch`：`src/llama.cpp/src/llama-context.cpp:4443`
- barrier 推算：`2026-09-23.md:580`、`2026-09-24.md:146,212,322,337`
- `n_main` 總時間守恆：`docs/CB_N_MAIN_BUCKETS_2026-09-26.md`、`NEXT_MILESTONES` §1
- pool 真曲線（09-25）：`scripts/run_server.sh` prod-new if 段（8G 12.06／6G 10.48／4G 7.67 t/s）
