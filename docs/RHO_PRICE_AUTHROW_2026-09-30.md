# ρ 價格的「權威 row」版（設計 ＋ 預註冊）— 2026-09-30

operator 2026-09-30：

> 把 ρ 的價格場次改到權威 row 上跑（harness bench、每臂同一格、乾淨窗、臂上無探針），
> 讓 12+ 這種數字有一天真的能入表。

這一頁把那句話寫成可執行件：**一個阻塞判詞**（`rho_price_authrow.py --check`）、
**四條現成命令**（`--plan`）、**一條預註冊判詞**（`--judge`）。

---

## 判決：NO_EFFECT（2026-10-01 12:42）—— 權威 row 的四條跑完了，價格量不出來

切分（`CGC_RHO`）落地後，`rho_window.py --go` 在**同一個乾淨窗**裡把 S5 的四條跑完（4/4），
`rho_price_authrow.py --judge` 對兩趟反序判 **`NO_EFFECT`** —— 判詞檔
`Backup/rho_price_2026-09-30/verdict.json`（build 當場重判，見看板 L20-7 的 `counter_quotes`）：

| 趟 | 臂 | 產物 | decode（t/s） | 逐 rep（3） | spread | 窗口 | 引用 |
|---|---|---|---|---|---|---|---|
| order1 | A（裸 `prod-new`） | `Backup/rho_price_2026-09-30/order1_A.json` | **12.3027** | [12.4478, 12.1916, 12.2687] | 1.021 | `none`／NOMINAL | QUOTABLE |
| order1 | B（`prod-new:CGC_RHO=1`） | `order1_B.json` | **11.6573** | [11.5416, 11.6667, 11.7637] | 1.019 | `none`／NOMINAL | QUOTABLE |
| order2 | A | `order2_A.json` | **12.3767** | [12.3939, 12.4962, 12.24] | 1.021 | `none`／MODERATE | QUOTABLE |
| order2 | B | `order2_B.json` | **12.1265** | [12.41, 11.8207, 12.1487] | 1.050／kept 1.028 | `none`／NOMINAL | QUOTABLE |

四條同格：`(default)`、`profile=prod-new`、`harness bench --batch 5632 --prompt 2048 --gen 128
--depths 512 --reps 3 --warm-skip 64 --ctx-size 0`；臂身分逐字（A 無 `CGC_RHO`、B 有）⇒ 這是
「**兩臂只差一顆交付旗標**」的權威 row，不是輪級聚合（舊的 `Backup/s3b_abba_2026-09-30/*.json`
仍判 R8c `REFUSE`）。

**判詞與讀法**

* order1：效應 **−5.25%** ＞ 兩臂散布 **2.10%** ⇒ 可解。
* order2：效應 **−2.02%** ≤ 兩臂散布 **4.99%** ⇒ 不可解。
* 兩趟**同向**（都是 B 較慢），但預註冊的 `PRICE` 要求**兩趟都可解** ⇒ 判 **`NO_EFFECT`**，
  中位 **−3.63%**。⇒ 正確讀法是「**ρ 不是免費；而這套設計（3 reps、解析度＝逐臂 max/min）
  量不出它的價格**」——**不可以**寫成「ρ 免費 ⇒ R5 可解」。
* 附帶證據（同一份產物的另一列）：prefill 通道的代價同向且更大（order1 pp **288.4 → 206.1**、
  order2 **281.1 → 206.2** t/s），與 `S3B_RHO_COST` §7 的舊結論一致；但它不是價格的判準。
* 兩條髒窗留檔可對帳：`archive/Q1.20261001-1038/`（舊 `order1_A`，thermal HEAVY）、
  `archive/Q3.20261001-1228/`（`order2_B` 首跑，thermal HEAVY）⇒ 依 R4 判 DIRTY，重跑後才定案。

**順手打破的天花板（12+ 首批）**

四條產品同時送 `caliber_certify.py --kind decode --target-ts 12` ⇒ **3/4 `CERTIFIED`**
（`order1_A` 12.30／`order2_A` 12.38／`order2_B` 12.13，都**嚴格高於**棘輪上限 11.982966；
`order1_B` 11.66 兩條都不過）。這是全語料**第一批 ≥12 且可認證的 decode 列** ⇒
**operator 2026-10-01 決定：入表＋推上限** ⇒ 看板已新增 **C9 12.3767**（order2_A，新錨）／
**C10 12.3027**（order1_A）／**C11 12.1265**（order2_B），`certify_anchor.decode_ts` 推到
**12.376702**（cell `(default)`；舊上限 C8 `11.982966` → 歷史值）。⚠ 這三條是 `(default)` 格，
與交付 cell 家族不可互比（§56）；棘輪的**數值**上限≠交付 cell 的基準。

**對 R5／後續的影響**

* **R5 仍未解除**：解除條件是「權威 row 上有一個**可引用的價格**」；這一格給的是 `NO_EFFECT`
  （量不出來），不是價格。
* 價格這條線在這裡**收在排除**（否證式）：要再往前走＝**新設計**（提高解析度／改用配對統計量），
  屬於口徑改革（前瞻卡 `scripts/check/charters/e-quote-caliber-paired-2026-10-01.yaml`），**不回填這一格**。
* 看板回填：L20-7 的 `precondition` 轉 `duplicate`、`verdict_exists`、`settled`，端點①（儀器）
  與端點②（價格）**兩個 `counter_quotes`** 都由 build 當場重跑（`decode_board_build.py` 的 D7b）。

---

## 1. 先講結論：這一條**現在跑不了**，而且是硬矛盾

> ⚠ **2026-10-01 更新**：本節是**切分前**的結論（當時 `--check` 判 `BLOCKED`）。切分已落地、
> 四條已跑完，判決見上面的 `## 判決：NO_EFFECT` 節；以下保留為當時的設計依據。

「臂上無探針」在今天的原始碼裡**等於「機制不在跑」** —— ρ 的三個執行者全部掛在 `CGC_RHO_PROBE` 底下：

| 座標 | 內容 | 後果 |
|---|---|---|
| `src/llama.cpp/src/models/qwen35moe.cpp:218-220` | 影子路由節點（`cgc_rho_logits-<il>`）只在 `getenv("CGC_RHO_PROBE")` 為真時才建 | 沒 probe ⇒ **圖裡沒有影子節點** |
| `src/llama.cpp/src/llama-context.cpp:4800-4801` | `expert_cache_eval_cb` 裡那一段（capture ＋ prefetch 呼叫）的閘就是 `cgc_rho_probe` | 沒 probe ⇒ **沒有 capture、也沒有 prefetch 的呼叫** |
| `src/llama.cpp/src/llama-context.cpp:4813-4844` | 唯一「做正事」的一行 `ctx->cgc_rho_prefetch(...)` 就在那個區塊內 | 機制整條不在 |
| `src/llama.cpp/src/llama-context.h:288-289` | 檔頭自述：「開關 `CGC_RHO_FILL`；**需要 `CGC_RHO_PROBE=1`**（圖裡要有影子節點）」 | 依賴是**引擎級**的，不是量具級的 |
| `scripts/run_server.sh:1780-1791` | 兩個旋鈕的語意（PROBE 建影子路由、FILL 把預測變成真的非阻塞 prefetch） | 追蹤片語在樹上 |

⇒ 今天若照字面跑「A：`prod-new` vs B：`prod-new:CGC_RHO_FILL=1`」而**臂上不帶 probe**，
B 臂只是**多了一顆被忽略的旗標** ⇒ 兩臂逐項相同 ⇒ Δ＝0。

**「假的 0」比「量不到」更糟**：它會被讀成「ρ 免費 ⇒ R5 可以解除」，而 R5 的解除條件是
「先在權威 row 上量出一個可引用的價格」。所以 `rho_price_authrow.py --check` 對這條設計
在今天的樹上判 **`BLOCKED`**（並逐一列出上面四個座標的行號）——不發車、不假裝。

```
$ python3 scripts/check/rho_price_authrow.py --check
  影子路由節點的建立閘                   src/llama.cpp/src/models/qwen35moe.cpp:220  …
  eval hook 裡的 capture＋prefetch 呼叫閘 src/llama.cpp/src/llama-context.cpp:4801  …
  capture／prefetch 的呼叫點             src/llama.cpp/src/llama-context.cpp:4844  …
  run_server.sh 的旋鈕語意               scripts/run_server.sh:1780  …
  CGC_RHO* 旗標: CGC_RHO_FILL, CGC_RHO_PROBE, CGC_RHO_PROBE_LATE
VERDICT: BLOCKED -- ρ 的影子節點與唯一執行者都掛在 CGC_RHO_PROBE 底下 …
```

⚠ `CGC_RHO_FILL` **不是**切分：它自己的作業就在 probe 區塊裡（`--check` 把它標成
`dependent_flags`，不列進 `delivery_flags`）。

---

## 2. 要切什麼（引擎工作項；立項卡 `charters/e-rho-delivery-flag-2026-09-30.yaml`）

1. **交付旗標**（本文以 `CGC_RHO` 為代號）：影子節點 ＋ capture ＋ fill 的閘；
   `CGC_RHO_PROBE` 退回**純量具**（只負責 `CGC-RHO-*` 記帳）。
2. **capture 的形狀**順手按 S2-c 的結論做對：**每步一次讀回**（不是每層一次）——
   §7.8 已把「每層一次同步」列為成本，且它是「帶探針的發射」那條尾巴的嫌疑之一（未定罪）。
3. **切分的驗收（三條都要）**：
   * 配對 oracle：M1 **9/9**（`m123_oracle_gate`；錨＝同 build 的誠實臂）；
   * 量具不消失：`CGC_RHO_PROBE=1` 時 `CGC-RHO-SUM` 照舊印（記帳與機制分家，但記帳還在）；
   * 量具不外洩：**只有** `CGC_RHO=1`（probe 不在）時，stderr **不得**出現任何 `CGC-RHO-*`
     （否則只是改名，沒有分家）。
4. ⛔ 不可動：prefill 端的相位閘（`llama-context.cpp:4801-4818` 的 PHASE-SKIP，2026-09-26
   的實測理由）、影子節點的位置（`inpSA = inpL`，必須比真實 `ffn_moe_topk` 早一個 submodule）。

---

## 3. 切完之後怎麼跑（預註冊；命令由 `--plan` 直接印）

**兩臂**（臂上**都沒有** probe）：

```
這條在切分後才成立：
python3 scripts/check/harness.py bench --charter scripts/check/charters/e-rho-delivery-flag-2026-09-30.yaml \
    --batch 5632 --prompt 2048 --gen 128 --depths 512 --reps 3 --warm-skip 64 --ctx-size 0 \
    --arm prod-new            --json Backup/rho_price_2026-09-30/order1_A.json
python3 scripts/check/harness.py bench … --arm prod-new:CGC_RHO=1 \
    --json Backup/rho_price_2026-09-30/order1_B.json
# 第二趟反序（B 先、A 後）⇒ order2_B.json / order2_A.json
```

* **同一格**：預設格（`(default)`：`-b/-ub 5632`、`p2048`、`gen 128`、`d512`、`reps 3`、`warm-skip 64`、`ctx 0`）。
  旗標向 `cell_contract.bench_flags()` 要（單一定義）；預設格**不傳** `--cell`
  （具名格表裡沒有 `(default)`，傳了會 fail-closed 拒跑）。
* **乾淨窗**：`attribution=none ∧ thermal=NOMINAL`、逐 rep 離散 ≤1.10 —— 這是 `quote_gate` 的 R3／R4，
  不是本文另立。
* **判詞（`--judge`，跑前寫死）**：
  1. 兩臂的 **decode 行**必須 `QUOTABLE`（R1–R8 全過）——任一不過 ⇒ `REFUSE`
     （不可引用的數字沒有價格）；
  2. 兩臂的臂上 env 差**只有** `CGC_RHO`（差多了 ⇒ 量到的是別的東西；差沒有 ⇒ 安靜的 null）；
  3. 每一趟的 |Δ| 必須**大於兩臂自身散布**（任一邊不過 ⇒ 該趟不可解）；
  4. 兩趟都可解且**同向** ⇒ `PRICE`（附 Δ 中位）；兩趟都不可解 ⇒ `NO_EFFECT`；
     可解但反向 ⇒ `REFUSE`（順序效應）。

**這是唯一能讓「12+ 那一族」變成可引用的路**：`quote_gate` 的 R5 擋 probe 臂、R8c 擋輪級聚合，
兩條合起來把今天所有 ρ 讀數都判不可引用（§8）；切分後 B 臂身上沒有 probe、產物是權威 row ⇒
才有可能 `QUOTABLE`。**不是**靠換一句話描述。

---

## 4. 今天能做／不能做（不假裝）

| 能不能 | 內容 |
|---|---|
| ✅ | `--check`：阻塞是機器判詞（四個座標在樹上，`--check` 判 `BLOCKED`；切分落地後同一支判 `RUNNABLE` ⇒ 看板/人回來重判） |
| ✅ | `--plan`：四條命令（cell 旗標即時向 `cell_contract` 要） |
| ✅ | `--judge`：判詞與 selftest（19/19；含「假 0 不是免費」「反向不是價格」「髒臂不是價格」三條否證） |
| ✅ | 切分補丁：`Backup/rho_split_2026-09-30/rho_delivery_split.patch`（**未套用**、可即刻 `git apply`；`--checklist` 印套用→建置→oracle→四條命令） |
| ⛔ | 發車：切分不在 ⇒ 今天跑的任何「無探針」ρ 場次都是安靜的 null |
| ⛔ | 引用：今天所有帶 probe 的 ρ 讀數、以及 `ab_interleave` 的輪級讀數，都不可入認證表（§8） |

---

## 5. 驗證與檔案

* `scripts/check/rho_price_authrow.py`（`--check`／`--plan`／`--judge`／`--checklist`／`--selftest` 21/21）
* `scripts/check/window_runner.py`（窗口**流程**：步驟以資料宣告、skip／fail-fast／可接續；
  `--selftest` 23/23）＋ `scripts/check/rho_window.py`（ρ 的**宣告**：S1→S6；`--status` 預設不執行；
  `--selftest` 41/41，含整段彩排）；兩支都已進 `harness.py REGISTRY`
* 立項卡：`scripts/check/charters/e-rho-delivery-flag-2026-09-30.yaml`
* 相關：`docs/S3B_RHO_COST_2026-09-30.md` §7.6（價格 `REFUSE`）、§7.8（尾巴與每步一次讀回）、
  §8（R8：輪級聚合不可入表）；`docs/R6_SEGBATCH_FIX_PLAN_2026-09-30.md` §4.5-4.6（S2-c：捕獲搬出 hook）

---

## 6. 切分補丁（已交付、未套用）：套用→建置→oracle→四條命令

`Backup/rho_split_2026-09-30/rho_delivery_split.patch`（md5 `37188900d3ba71369ea80478d1a60273`、
11975 B、11 hunks、4 檔：`llama-context.cpp` ＋ `llama-context.h` ＋ `qwen35moe.cpp` ＋ `run_server.sh`）。
機械件：`rho_price_authrow.py --checklist`（清單）與 `--check`（套用後轉 `RUNNABLE`）；
逐項結果存 `Backup/rho_split_2026-09-30/delivery.json`。

**切什麼**

* `CGC_RHO=1` ⇒ **交付面**：影子節點＋capture＋fill 一起（`cgc_rho_prefetch` 的閘是
  `cgc_rho_deliver || CGC_RHO_FILL`）；stderr **不印**任何 `CGC-RHO-*`。
* `CGC_RHO_PROBE=1` ⇒ **量具**（印帳）並向後相容地把機制一起帶上 ⇒ 既有探針臂逐位元不變。
* `CGC_RHO_FILL` 語意不變（只控制「要不要真的發 IO」）。
* ⛔ 不動相位閘（`cgc_is_decode_graph`）與影子節點位置（`inpSA = inpL`）—— 只換「哪個旗標開它」。

**逐項驗過（不套用；只對 scratch 樹）**

| 檢查 | 結果 |
|---|---|
| `git apply --check -p1` 對**未修改的實檔** | PASS |
| `patch -p1` 進 scratch 樹後與交付內容 `cmp` | **4/4 byte-identical** |
| `-fsyntax-only`（專案 `compile_commands.json` 的原命令） | 兩檔 rc=0；**新增行內 0 warning**（`llama-context.cpp` 9 條、都在別處；`qwen35moe.cpp` 0 條） |
| `rho_price_authrow.py --check --root <套用後的樹>` | `RUNNABLE`；旗標表出現 `CGC_RHO`；交付路徑 **4/4** 列印由 `cgc_rho_meter()` 守著 |
| 交付旗標轉送（launcher allowlist） | `run_server.sh:1799` 命中 —— oracle 臂 `--env CGC_RHO=1` 的前提 |
| `rho_price_authrow.py --selftest` | **19/19**（含「拿掉量具閘 ⇒ `SPLIT_LEAKY`」的否證） |
| `instrument_binding.py --self-test` | ALL PASS（`CGC_RHO_FILL` 的前提改成 OR：`CGC_RHO` 或 `CGC_RHO_PROBE`） |

**落地狀態（交付當下；同一支 `--checklist` 自己推、自己印）**

* **套用：否** —— 這一棵樹的機制閘仍是 probe-only（`--check` 判 `BLOCKED`），且四份 base md5 讀回
  **逐字未變**（`bc65fbb4…`／`87bd9bfb…`／`d8b1fa90…`／`17e3d16f…`）⇒ `src/` 沒被這份交付碰到。
* **建置：否** —— `build_state()`：`src/llama.cpp/build/bin/libllama*.dylib` 16 顆，精確 `CGC_RHO`
  字串命中 **0** 顆（`CGC_RHO_PROBE` 在 ⇒ 現在跑的是切分前的 build）。
* **GPU：0 趟** —— 清單本身不發車（沒有 `harness bench`／server／`m123`），且未套用未建置 ⇒ 不可能
  有任何一趟用到切分。
* **主樹 0 痕跡** —— `~/Documents/flashkv0516` 沒有 `Backup/rho_split_2026-09-30`、`rho_price_authrow.py`、
  這張卡、這份文件；它的 `git status` 也沒有本輪碰過的任何路徑（本輪改的工具檔在該樹根本不存在）。

⇒ 這一段不是敘述：套用與建置兩條在**同一支 `--checklist`** 裡由樹與 build 產物讀出來（套用後轉「是」、
重 build 後轉「是」），GPU 那條由「清單不執行任何東西」保證。

**為什麼補丁要動 `run_server.sh`**：`m123_oracle_gate` 的兩臂都經 `run_server.sh` 解析啟動環境
（那支檔自己就叫它 allowlist 陷阱）；沒轉送 ⇒ `--env CGC_RHO=1` 到不了引擎，見證臂會退化成
「安靜的 null」那一族。四條權威 row 命令（`harness bench`）不吃 launcher。

**收貨條件（跑前寫死；`rho_window.py` 的 receipt 就是這五條）**

1. `--check` 轉 `RUNNABLE`（切分落地）；
2. **旗標真的進了子行程**：交付臂 summary 的 `extra_env['CGC_RHO'] == '1'`，而對照臂的 `extra_env`
   **沒有** `CGC_RHO`（這一條才是補丁第五個座標 `run_server.sh` 轉送的收貨；讀的是
   `run_server.sh CGC_DUMP_ENV=1` 解析出來的**生效覆蓋集**）；
3. **量具不外洩**：交付臂的**伺服器 log**（`launch_<tag>.log` 那條 banner 的 `[log] <path>` 指名
   的那一份）`grep -c 'CGC-RHO-'` ＝ 0。⚠ **不是** grep `launch_<tag>.log` 自己——那只是 banner，
   永遠是 0，會得到一個**假的綠**；且這條是**必要不是充分**（探針臂的伺服器 log 在 teardown 沒跑到時
   也是 0 行，實測 `s2b-arm-segbatch-v9`：`CGC_RHO_PROBE=1` 而 rho=0）；
4. oracle **M1 9/9**（切分不動數值）；錨另須 `M1=M2=M3=n`；
5. 四條命令跑完 `--judge` 判 `PRICE`，否則 `NO_EFFECT`（＝這套設計量不出來，合法結局）／`REFUSE`
   （不是結局，要修）；**任何情況都不得**寫成「ρ 免費」。

池的 `prefetch=N/M` 是**池**的計數器（所有 prefetch 路徑共用）⇒ 「N≥1」只能證明池在預取。ρ 的貢獻
是 S3（對照臂）與 S4（交付臂）兩次啟動的**差分**（同一 binary、同一份 prompt、只差一顆 `CGC_RHO`）：
差分 > 0 是「交付旗標真的多發了預取」的證據；差分 = 0 時要查 `cgc_rho_prefetch` 的層容量閘
（union 裝不下 ⇒ 整層讀，不是預取），**不能**寫成「機制沒跑」。價格仍然只有 S5 的權威 row 給得出。

**窗口腳本：一條命令把 S1→S6 做完（流程 `window_runner.py` 23/23 ＋ 宣告 `rho_window.py` 41/41）**

流程與宣告**分開**：skip／fail-fast／可接續是每個「卡在窗口」的子目標共有的形狀，所以它住在
`window_runner.py`（單一定義）；ρ 只宣告「跑什麼、收什麼、失敗看哪裡」。要掛一個新的子目標：

```python
import window_runner as WR
STEPS = lambda ctx: [                       # 步驟是資料；也可以是清單
  dict(id="S1", name="套用補丁", commands=[[...]],
       done=lambda r, c: ...,               # 跑前掃產物：已在 ⇒ (True, why) ⇒ skip
       receipt=lambda r, c: ...,            # 跑後驗收（沒給就用 done）
       next_on_fail=["…先看哪一支工具／哪一節…"]),
  dict(id="S2", name="四條命令", commands=[           # 一步裡可以有好幾趟
       dict(cmd=[...], skip_if=lambda r, c: (..., "已在"), log="Backup/…"), …]),
]
WR.main(STEPS, preflight=my_preflight, journal="Backup/…/window.jsonl",
        log_dir="Backup/…/window_logs", title="…", ctx_factory=my_flags)
```

```bash
python3 scripts/check/rho_window.py            # --status（預設）：只印計畫與現況，不執行任何東西
python3 scripts/check/rho_window.py --go       # 真的跑：任一步失敗就停，並印「下一個該看的東西」
python3 scripts/check/rho_window.py --go --only S1,S2                    # 只套用＋建置
python3 scripts/check/rho_window.py --go --reuse-ctl Backup/m123_oracle_gate/summary_<既有錨>.json
```

| 步 | 做什麼 | 收貨（由產物判，不由 rc 判） |
|---|---|---|
| S1 | `git apply` 補丁（`md5` 對不住 ⇒ 拒跑） | `--check` 轉 `RUNNABLE`、4/4 交付列印在 `cgc_rho_meter()` 裡 |
| S2 | `cmake --build`（`llama-server`＋`llama-bench`） | `libllama*.dylib` 內**精確** `CGC_RHO` 字串出現 |
| S3 | oracle 對照臂（錨） | 錨 `M1=M2=M3=n`、`comparable`、`extra_env` **無** `CGC_RHO` |
| S4 | oracle 交付臂 `--env CGC_RHO=1` | M1 9/9、`extra_env['CGC_RHO']=='1'`、伺服器 log 零 `CGC-RHO-*`、印出池差分 |
| S5 | 四條 `harness bench`（兩臂 × 兩趟反序，同一格） | 四份產物各自 decode 行 `QUOTABLE`＋成對 stderr log＋零 `CGC-RHO-*`＋**A 臂無／B 臂有** `CGC_RHO` |
| S6 | `--judge` 兩趟反序 ⇒ `verdict.json` | `PRICE` 或 `NO_EFFECT`（`REFUSE` 不算完成） |

規矩：**預設不執行**（`--status`，rc=2 表示還有 pending）；**跑前先掃產物**（已在就 `skip`，不重跑）；
**收貨讀對檔**（oracle 讀 banner 指名的那份伺服器 log；bench 讀產物的成對 log——判準住
`instrument_binding.pair_status()`）；每一步 append 進 `Backup/rho_split_2026-09-30/window.jsonl`
⇒ 中斷後重跑從沒完成的那一步接續；每次失敗都附**具體**的下一手（哪支指令、哪個檔、哪一節）。
`--status` 開頭會報`src/llama.cpp` 還有幾個未提交的檔（**別線還在佔著 src/ 的訊號**）——不是 blocker
（`git apply` 是原子的，衝突就當場失敗），但**建置會混用半成品**，所以窗口是不是你的由人決定。
`--selftest` 含一段**整段彩排**：fixture 的 S1/S2 ＋ 假 runner 寫出真形狀的 S3–S6 產物 ⇒ 六步跑完、
`verdict.json` 落地、日誌六步到齊（這一條驗的是流程本身，不是任何數字）；流程那一半自己的體檢
（契約拒收、預設不執行、fail-fast、接續、逐趟 skip、`--only`、`preflight` 拒跑）住
`window_runner.py --selftest`（25/25，全部走一個玩具宣告 —— 那就是「別的子目標也掛得上」的自証）。

**影子模式（零 GPU，可重複）：`--shadow`**

`python3 scripts/check/rho_window.py --shadow --status`（只看，預設不跑）／`--shadow --go`（真的跑）。
四步走同一套 `window_runner` 核心：產物掃描 ⇒ skip、每步 receipt、失敗即停並印下一手、journal 可接續。

| 步 | 做什麼 | 收貨 |
|---|---|---|
| P1 | 複製 `src/llama.cpp`（乾淨複本，保留 `build/`）、`scripts/`、補丁，`models` 連線 | 影子備妥且**不比實樹舊**（`rsync -a` 帶 mtime ⇒ 複本裡最新的 mtime 就是「它從實樹哪個狀態複來的」；實樹更新就重做一次乾淨複本） |
| P2 | `git apply -p1 --directory=<shadow>`（**只進影子**，實樹不動） | `--check --root <shadow>` 判 `RUNNABLE`；交付列印洩漏 0 條 |
| P3 | 跑**影子自己那份** `scripts/build_fork_llama.sh`（`CGC_BUILD_PROFILE=devserver`） | 產物含精確 `CGC_RHO` ＋ **脚本寫下的 profile 戳記** |
| P4 | 足跡與證據落檔（in-process） | `Backup/rho_split_2026-09-30/shadow.json` 判「最新」 |

`--status` 現在是**核心明列的旗標**（＝預設；與 `--go` 並用就拒收 —— 不是靜默選一個）；`--shadow`
模式不認得的旗標也直接拒收（先前會靜默忽略 ⇒ 打錯一個字就變成「驗了別的東西」）。

**P3 的完成條件是「照建置脚本建完了沒」，不是「有沒有產物」**——憑據是脚本自己在最後寫下的
`CGC_BUILD_PROFILE.txt`。只有產物沒有戳記 ⇒ P3 不算完成 ⇒ 下一次 `--go` 用同一份脚本、`REBUILD=0`
接續（分幾輪都跑得完，不怕被中途砍）。`--shadow-rebuild` 再往前一步：`REBUILD=1`、連脚本自己的
「已完成」都一起重來（selftest 兩條釘住它）。

**實跑結果（2026-09-30 23:19–23:26）**

| 收貨 | 結果 |
|---|---|
| **S1** | `--check --root <shadow>` ⇒ **`RUNNABLE`**；四份 base md5 與補丁逐字相同；`git apply -p1` 乾淨套上；交付路徑 4/4 列印由 `cgc_rho_meter()` 守著；`run_server.sh:1799` 命中 |
| **S2** | 影子自己的脚本 configure＋`cmake --build` 真的跑到 **100%**（`llama-server`／`llama-bench` 都在）：`libllama*.dylib` 命中**精確** `CGC_RHO` **3 顆**；戳記 `profile=devserver … MTP_SUPPORT=ON`、`git_head=190cc69c…`、`git_dirty_files=239` |
| 足跡 | 影子 **526 MB**（build 240 MB）；磁碟剩 28.0 GB；約 **7 分鐘**（含脚本的 build-all：tests＋tools＋app） |
| S3–S6 | 維持 pending —— oracle 與四條 bench 要真的盒子，這裡不假裝 |

**影子驗抓到的第一件事（已寫進 `NEXT_ON_FAIL['S2']`）**：第一次 configure 用了我手抄的旗標 ⇒
`llama-bench.cpp:2763` 編不過（`release_context()` 在 `#ifdef MTP_SUPPORT` 裡）。正解是照這個 repo
自己的 `scripts/build_fork_llama.sh`：Mac 的 configure **必須**帶 `-DCMAKE_CXX_FLAGS=-DMTP_SUPPORT`
—— 也就是說，這一步的「重現指令」不能憑記憶寫，要從建置脚本要。⇒ 所以 P3 跑的是**複製進影子的
那一份脚本**（它的 `FORK_DIR` 由脚本自身位置推得 ⇒ 旗標清單與 profile 戳記都是同一份），現在這一步
的重現指令是**零手抄**的。

⚠ 脚本自己的 log 照實記進證據：C++ 建置到 100%，但脚本的 UI 段在這台機器上是壞的（`npm error
EBADENGINE`：node v18 對不上 storybook 的 >=20；`UI: download dist.tar.gz … failed`）——**不擋**
C++ 產物，脚本也會繼續走到戳記。`shadow.json.build.script_log` 把這幾項逐一記下（不假裝全綠）。

⚠ 影子樹住在 repo 裡、不是 git repo ⇒ `_src_dirty()` 必須確認 `rev-parse --show-toplevel` 就是這一棵
（否則會把母樹的 239 個未提交檔記到影子樹頭上）—— 這條已修並有 selftest。

**證據落檔**：`Backup/rho_split_2026-09-30/shadow.json`（`prepare`／`apply`／`build`：戳記、旗標
（從**影子自己的** CMakeCache 讀、不是從意圖）、帶 ρ 的 dylib md5、兩顆二進位的 md5、脚本 log 摘要
／`footprint`／`box`／`receipts`）；逐輪軌跡 `shadow_journal.jsonl`；各步 log `shadow_logs/`。

⚠ 「12+ 入表」仍要同一件事的全部口徑：同 cell、權威 row、逐 rep 向量、乾淨窗、臂上無 R5/R6；
`--judge` 的第一條就是兩臂的 decode 行各自 `QUOTABLE`。

⇒ **2026-10-01 起有機器可走**（口徑統一）：`python3 scripts/check/caliber_certify.py certify <產物>
--target-ts 12`（四條判準的唯一落點：`caliber_gate` UNIFIED ∧ `quote_gate` R1–R8 ∧ 達標 ∧ 格子仍在）——
判 `CERTIFIED` 就吐可直接貼進看板 `certified:` 的列，不必再逐件裁定；判 `REFUSED` 就逐條列出差在哪。
預註冊：`docs/CALIBER_UNIFIED_PREREG_2026-10-01.md`；全樹帳：`Backup/caliber_unified_2026-10-01/audit.json`
（2026-10-01：可引用 decode 天花板 **11.98** ⇒ 目前 **0 個 12+**）。
