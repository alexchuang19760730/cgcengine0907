# 跑中 swap 成長成為閘門（2026-09-28）

> 需求：臂結束時若 **swap 成長超過預算** 或 **free 觸底**，該臂即判**不可用**，並在產物裡留下**成長曲線**。
> 結果：成長那條**有牙且已校準**；free 那條**被自己的校準否證**，因此預設關閉（理由寫在產物裡，不是默默不設）。

## 0. 一句話

`memory_pressure.SWAP_GROWTH_MB = 512` 這條門檻**從來沒被校準過**（模組自己的 docstring 就這麼說，並指名
`.workbuddy/memory/swap_log.tsv` 當校準集）。實際上那份檔案只有 **5 列單點存量**、沒有臂邊界，**不能用**。
這一輪改用 repo 自己累積的**跑中曲線**去校準（掃 ~2.3k 個 JSON，**127 支臂**帶量測曲線），得到三件事：

1. **成長與 decode t/s 單調相關**，而 `512 MiB` 正好落在膝點上 ⇒ 可以當閘門。
2. **`pages_free` 不具區辨力**（乾淨與污染的桶，p10–p90 都是 **14–50 MiB**）⇒ 絕對 free 地板會拒絕
   幾乎**全部**歷史，卻分不出好壞。**這條判準不能照原樣實作。**
3. 同一支臂的成長本身變異很大（見 §4），這正是它值得當閘門的原因。

## 1. 校準（n=127 支有曲線的臂，分成兩個 cell）

```
cell prod-new (n_prompt 2048 / n_batch 5632, n=111)
   growth 桶      n    decode tg 中位數   p25    p75
      0-512      23        11.96       11.61  20.06
   512-2048      45        11.54       10.45  11.98
   2048-8192     41        10.64        9.09  11.64
     >8192        2         8.02        5.62   5.62

cell delivery (n_prompt 0 / n_batch 512, n=15)
      0-512       5        11.38        7.46  11.38
   512-2048       5        10.44       10.32  10.44
   2048-8192      5         9.14        8.47   9.14

兩者的 min_free 百分位（每桶都一樣）
   p10=14 MiB   p50=14 MiB   p90=50~57 MiB   max=57 MiB
```

**成長**：`0–512` 桶到 `2048–8192` 桶差 **−11%**（prod-new）與 **−20%**（delivery），`>8192` 差 **−33%**。
單調、兩個 cell 同向 ⇒ 門檻設在膝點 `512 MiB` 是**有依據的**（而且它本來就是模組常數，現在才被證實）。

**free**：`p10` 到 `p90` 只跨 14→57 MiB，而且**在最快的 23 支臂（growth 0–512）裡照樣是 14 MiB** —— 這台盒子
只要有一個 9 GB 模型＋8 GiB expert cache 進駐，free 就會到那裡並停住。所以「free 觸底」在這個
量上**不是污染的訊號，是駐留的常態**。

⇒ 實作取捨（**兩條都在產物裡說明白**）：

| 判準 | 預設 | 為什麼 |
|---|---|---|
| `peak_growth > 512 MiB` | **開**（閘門） | 膝點、單調、兩 cell 同向 |
| `wired_growth > 1024 MiB 且伴隨 swap 成長` | **開** | L4 crowding 形狀（沿用 `WIRED_GROWTH_MB`） |
| `min_free < 門檻` | **關**（`FREE_FLOOR_MB = None`） | 校準顯示不具區辨力；`curve_gate` 回傳 `free_criterion` 明確記下「off + 為什麼」，而不是讓它看起來只是沒設 |

若還是要開 free 地板（例如換一台記憶體行為不同的機器），`CGC_FREE_FLOOR_MB=<MiB>` 可開，
它會出現在產物的 `budget.overridden` 裡。

## 2. 閘門放在哪、怎麼判

- **放在 matrix**（`llama_bench_matrix.run_arm`）——那是**曲線被量出來的地方**。放在請求端等於
  「只有那個入口有牙」（`cell_contract` 也是為了同樣理由放在 matrix）。
- 判詞寫進產物：`record["memory_gate"] = {ok, reasons, growth_mb, peak_growth_mb, peak_swap_mb,
  peak_at, min_free_mb, wired_growth_mb, n, budget, free_criterion, curve, sparkline}`。
- **rc=4**（與 `incomplete` 的 rc=1、internal-guard 的 rc=2 區分開）。所以
  `harness bench` → `commit_bench` → `rep_split.py` 全部繼承，不需要各自重寫一次判準。
- **fail-closed 於「讀不到」**：swap/free 讀值有缺 ⇒ **不可用**（缺量測不是通過）。`rep_split` 的
  refusal 訊息會把預算理由接在 rc 後面（「rc=4」單獨不告訴任何人原因）。
- **逃生口**：`CGC_IGNORE_SWAP_BUDGET=<理由>`。臂維持可用，但**理由與原始 `reasons` 都留在產物裡**
  （waiver 不抹掉證據）。

## 3. 成長曲線留在哪

| 位置 | 內容 |
|---|---|
| `record["memory"]["samples"]` | **原始**逐取樣序列（`interval_s` 1.0s），每點 `{t, swap_used_mb, pages_free_mb, pages_wired_mb, llama_procs}` —— 這條本來就在了，這一輪只是把它變成有牙的判準 |
| `record["memory_gate"]["curve"]` | 摘要（≤12 點，**首、尾、峰值必留**） |
| `record["memory_gate"]["sparkline"]` | 一行文字曲線，讓 log 就看得出形狀 |
| `rep_split` 合併紀錄的 `rep_split.evidence[].memory_gate` | 每次啟動的判詞（含曲線摘要），外加 session 級 `rep_split.memory{all_ok, max_peak_growth_mb, min_free_mb, budget}` |

## 4. 真實演示（同一支臂、同一 cell、同一盒）

```
A) 預設預算（512 MiB）      launch 1: rc=4  peak_growth=983 MiB  min_free=49 MiB
                           曲線 ▁▁▁▃▅▅██████  ->  SESSION 拒絕（rc=3，不寫出部分 arm）
B) 嚴格預算（100 MiB）      launch 1: rc=4  peak_growth=312 MiB  min_free=53 MiB
                           曲線 ▃▃▃▁▁▆▆▆▇▇█▇  ->  SESSION 拒絕，訊息指名：
                           「run-internal swap 成長 +312 MiB > 預算 100（end-launch +256 MiB，
                            峰值在 15:44:17，31 個取樣）…校準：>512 MiB 對應 decode t/s 中位數 -10~20%」
```

**A 是沒預期到的結果，而它才是重點**：同一支臂今天早上的三次啟動成長是 **+202 / +11 / −32 MiB**
（那時預算還不存在），現在同一支臂同一 cell 是 **+983 MiB**。⇒ **同一個配置的跑中成長本身就有
200 MiB → 1 GB 的擺盪**，而擺盪的那一端正是 contamination。所以閘門會**真的拒掉真臂**，不是裝飾。

## 5. 操作後果（需要你決定的一件）

**這台盒子現在的標準狀態過不了 512 MiB 這一關**：

- 歷史 delivery cell 的成長 p50 = **883 MiB**、p90 = 3494 MiB（n=15）⇒ 512 MiB 這一關會拒掉**約三分之二**的
  交付臂。
- prod-new（預設 cell）p50 = 1526 MiB ⇒ 幾乎全拒。

兩條路：

1. **保留 512 MiB 當認證門檻**（推薦）：它是 t/s 曲線的膝點，而「跑出一個過得了這一關的臂」＝
   「在一個夠乾淨的盒子上量」—— 那正是可以引用的前提。代價：跑之前得先讓盒子乾淨（關掉吃 6 GB 的
   應用程式、或重開機），否則 session 會拒。
2. **改成 per-cell 的 p50**（delivery 900／prod-new 1500）：通過率高，但它等於
   **「比中位數髒的臂才拒」**——會放行一半的污染臂，閘門變成中位數濾波器。

**沒有改動**：`attribution`（thermal/swap/both 那套判詞）保持原樣，仍在產物裡；這一輪新增的是
**同一個證據的另一個用途**（判可用性），不是取代它。

## 6. 改了哪些檔與驗證

| 檔 | |
|---|---|
| `scripts/check/memory_pressure.py` | `FREE_FLOOR_MB=None`、`budget()`（含 env 覆寫與「覆寫也記錄」）、`curve()`（首尾峰值必留、≤12 點）、`sparkline()`、`curve_gate()`；docstring 換成校準結果。selftest **+14 案例 → 32/32** |
| `scripts/check/llama_bench_matrix.py` | run_arm 內建 `memory_gate` ＋ 印判詞與曲線；`main` 依它回 **rc=4**（缺 gate 資料也算失敗） |
| `scripts/check/rep_split.py` | 逐 launch 讀判詞、拒絕訊息帶上預算理由、合併紀錄的 evidence 與 session 摘要。selftest **15/15** |
| `docs/SWAP_GROWTH_GATE_2026-09-28.md` | 本文件 |

驗證：`memory_pressure` 32/32（含「讀值缺席 ⇒ 不可用」「峰值不因回落到預算下而放行」
「waiver 不抹掉理由」「降採樣保留峰值」）、`rep_split` 15/15（含「rc=4 ⇒ session 拒且指名理由」）、
matrix/memory 編譯與 selftest ok、**兩次真實 session 演示**（§4）。

## 7. 備選槓桿「關掉吃記憶體的應用程式」——已量測，**它不是槓桿**

§5 第 1 條建議「跑之前先關掉吃記憶體的應用程式」。這一節把那句話量掉（純量測，未改碼）。

**全機盤點**（按 `.app` 聚合，280 個非 `.app` 程序另計）：

| 目標 | RSS | 可動性 |
|---|---|---|
| `WorkBuddy.app`（15 程序） | **2594 MiB** | 使用者自己的工作 app ⇒ 由使用者決定（本次保留） |
| `Freebuff.app`（7 程序） | 1116 MiB | **宿主鏈，不可動**（關掉即關掉量測的那個 session） |
| Clash Verge 54／AvastSecureLine 14／Doubao 3／Weather 3 | ≤54 MiB | 不具意義 |
| 系統程序（280 個合計） | 1827 MiB | 不是應用程式 |
| llama／ollama／docker 殘骸 | **none** | 這條線是乾淨的 |

⇒ **本機唯一可動的非宿主程序只有一個 `WebKit.WebContent`，208 MiB。**

**處置與讀數**：`SIGTERM 1507`（208 MiB）⇒ free **+203 MiB**（≒RSS）、swap **−16 MiB**；30 秒內未重生
（父為 `launchd`，客戶端 app 沒在要求它）。

**但這是雜訊，證據是同一段時間盒子自身的漂移。** 兩個量測命令之間（無人啟動任何東西）：

```
15:46   swap 5548/7168   free 4124 MiB
  ↓（幾分鐘、無任何啟動）
15:51   swap 4584/6144   free 2260 MiB      ⇒ swap −964 MiB、free −1864 MiB

閒置後 30 秒追蹤：swap 4568 → 4568（跨度 0 MiB）、free 跨度 61 MiB
```

⇒ 盒子自己的背景活動（與量測本身）在**分鐘尺度上擺盪 ~1 GB**，而我們能砍掉的是 **208 MiB —— 約 0.04×**
的 512 MiB 預算。而且**存量不是跑臂的那個變量**：入夜以來 swap 從 9733 → 4568 MiB（≈ −5 GB），
**沒有任何人關掉任何 app** ⇒ 那是 macOS 自己回收的。閘門量的是**臂期間的成長**，不是存量。

**結論（並更正 §5 的措辭）**：§5 寫「關掉吃 6 GB 的應用程式」是誤導 —— **可動的大戶在本機根本不存在**，
6 GB 那類是使用者自己的工作 app。這條槓桿的值是 208 MiB，實務上等於 0。

**它帶出來的真問題（未處置）**：閘門是「**一個會自己漂 ~1 GB 的量的差**」。同一族現象有兩個獨立樣本：
(1) 同一支臂三次啟動的成長 +202／+11／−32 MiB（§4 為 +983）；(2) 今天 swap 在兩個命令之間 −964 MiB。
若盒子在臂期間回收 swap，**成長會被低估、甚至變負** —— 一個真的污染臂可以靠「盒子同時在回收」而通關。
要補的是一個 control：同窗的 idle 漂移量，或一次 no-op window（同樣的取樣節奏、不跑模型）。

## 8. 把這個儀器本身拿去檢定：**growth 不是污染偵測器，是盒子清潔度偵測器**

在「今晚最乾淨的一刻」跑了一支真正的 session（free 6.1 GiB、thermal NOMINAL、無其他 llama 程序、
WorkBuddy 保留），cell `delivery-repsplit`、k=3、3 次啟動：

```
launch 1/3: rc=4  wall=43.9s  thermal_after=NOMINAL
  memory gate: UNUSABLE peak_growth=1970.7 MiB (budget 512.0) min_free=13.97 MiB
  swap 曲線: ▁▁▂▂▆▆█████▇   (32 取樣，峰值在 16:01:16)
⛔ REP-SPLIT SESSION 拒絕（rc=3，不寫出部分 arm）
```

**這支臂在其他每個維度上都是合格的**（所以拒它的是**且僅是**記憶體這一關）：

- 逐階段 ACCEPT：`phase=warm_skip drafted=79 mean_len=2.3030`／`phase=timed drafted=90 mean_len=1.8864`
  ⇒ 那一個 measured rep 的 draft 鏈**活著**。
- `llama_decode[0] returned -1` **2 次**（§`MTP_DRAFT_FIRST_STEP` 修前是 264）。
- thermal 62/62 NOMINAL（`worst` 也是 NOMINAL）；log 名 `p0_n128_d512_r1` ⇒ 單 rep 啟動；合約 cell 相符。

### 這不是「盒子不夠乾淨」，而是那個量測方向反了

`memory.samples`（1 Hz）的**首末取樣**：

```
起跑 16:00:50   swap 5483.7/7168   free 5065.9 MiB   wired 1736.0   llama_procs 0
結束 16:01:21   swap 7306.7/8192   free 6842.4 MiB   wired 1670.8   llama_procs 0
                                        ↑ 結束時 free 比起跑還多 1777 MiB
```

結束時 free **更高**、swap **更高**——這就是成長的機制：這支臂要放 13.6 GB 的模型（＋8 GB expert cache budget）
進一台 16 GB 的盒子，於是 macOS 把**起跑時還駐留在 RAM 裡的東西**推去 swap；臂結束後它的記憶體還回來，
但被推出去的頁面不會自己回來 ⇒ swap 不降、free 反而更高。
**所以 growth 量的是「起跑時盒子裡有多少東西可以被推出去」，不是「這支臂被別人干擾了多少」。**

### n=129 支臂的檢定（`Backup/**/*.json` 中所有「有跑中曲線＋decode 樣本」的臂）

| 關係 | Spearman rho |
|---|---|
| **growth × 起跑 `pages_free_mb`** | **−0.870** |
| decode t/s × growth | −0.376 |
| decode t/s × 起跑 `pages_free_mb` | +0.257 |
| decode t/s × 起跑 `swap_used_mb` | +0.038 |
| decode t/s × 跑中 `min_free` | +0.263 |

**`−0.870` 是這一節的重點**：growth 幾乎是「起跑時有多少 free」的確定函數。**盒子越乾淨 ⇒ 成長越大**
（因為有東西可推），而**已經被 swap 光的盒子成長≈ 0**（沒東西可推）。於是 `growth > 512 ⇒ 拒` 這道閘門
實際上是「**拒掉在乾淨盒子上跑的臂**」。同一個算式的原始用途（桶中位數）方向仍在
（成長 0–512 → 12.12 t/s；2048–8192 → 10.69），但**桶中位數混了配置差異**（129 支是不同 profile／旗標），
而 rank 檢定說：真正被 growth 編碼的是盒子狀態。

最乾淨的同臂對照（**同一支臂、同一 cell、同一盒、同一天**）——growth 跨 100 倍、且可達負值：

| 來源 | growth | decode t/s |
|---|---|---|
| `rep_split_2026-09-28/arm_k3.json`（3 次啟動） | −32 / +11 / +202 MiB | 7.77 / 9.83 / 9.86 |
| `swap_growth_gate_2026-09-28/launch01_with_curve.json` | +312 MiB | 9.54 |
| `swap_growth_gate_2026-09-28/launch01_default_budget.json` | +983 MiB | 9.83 |
| 本節（最乾淨的一刻） | **+1971 MiB** | **8.74** |

**負的「成長」是決定性的**：一個污染量不可能為負 ⇒ 這個差不是那個臂的性質。而最低的 t/s（7.77）
發生在 growth ≈ 0 的那一次。

### 對協定的意思

1. **這道閘門現在無法在乾淨盒子上認證任何臂**，而它會放行「盒子已經被 swap 光」的臂。§5 那兩條選項
   （保留 512 vs 改成 per-cell p50）**都不是這一題的答案** —— 問題不在門檻值，在**被量的是什麼**。
2. 直接可用的替代是把閘門放在**起跑狀態**（它才是 growth 真正的內容，rho −0.87）：起跑
   `pages_free_mb` / `swap_used_mb` 過不了就不要浪費 44 秒；這是**跑前**可判的，而現在這道是跑完才判。
3. 若仍要保留「跑中」閘門，它必須是**差分**而非絕對量（同窗 control／no-op window），或改用有因果故事的
   跑中徵兆（`min_free` 的時間積分；rho +0.263，獨立在 growth 之外）。
4. 本節**未改任何碼**：閘門維持原樣、證據與數字在上面。要不要換掉它是一個決定。

證據：`Backup/swap_growth_gate_2026-09-28/gatecheck_cleanbox/`（launch01.json、harness.log、
llama-bench stderr）。

**`rc` 語意確認**：拒絕回 **3**（`EXIT_OK, EXIT_USAGE, EXIT_REFUSED = 0, 2, 3`），selftest 有斷言；
本輪我第一次把 `$?` 用 `echo` 吃掉了（同一族錯誤），所以那行 `SESSION rc=0` 是我的量測錯誤，不是工具的。

## 9. 把閘門換成起跑狀態（並更正 §8 寫反的那條機制）

### 9.1 先更正單文件：§8 的因果方向寫反了

§8 我寫「盒子越乾淨 ⇒ 成長越大」。**錯。** n=122 的實測說的是相反：

| 起跑 free 四等分 | n | growth 中位 | growth p90 | t/s 中位 |
|---|---|---|---|---|
| 66–1993 MiB | 30 | **3372** | 5414 | 11.39 |
| 2323–5163 | 30 | 2298 | 3494 | 10.69 |
| 5240–7298 | 30 | 867 | 2087 | 11.58 |
| 7323–10859 | 32 | **342** | 1005 | 12.26 |

**free 越小 ⇒ 成長越大**（rho = −0.870），而兩段模型（見 §9.4）說得更精確：
`期望成長 ≈ max(226, 3914 − 0.4404·free)`——空機那一端有一個 **226 MiB 的地板**（臂自己的冷駐留：
模型頁、expert cache、Metal buffer），它不隨 free 繼續下降。機制是直白的：這支臂要放 13.6 GB 的模型進 16 GB 的盒子，
**起跑時還駐留在 RAM 裡的東西就是它必須推出去的東西**——所以那不是「別人在干擾」，是臂自己的載入成本。
§8 的結論（「閘門拒的是乾淨盒子」）因此也是錯的：**舊的絕對成長桿子實際上要求起跑 free ≳ 8.5 GB**
（`growth ≤ 512` 的那 27 支臂，起跑 free 中位 8518 MiB），也就是「幾乎空機」。那不是反的，是**太嚴**，
而且它把臂自己的駐留（expert cache 大小、batch）也算進去。

### 9.2 為什麼不能直接把「成長」丟掉

在校準裡有一件事讓答案不是那麼簡單：**起跑狀態解釋不掉的那部分仍然有訊號**。
以 122 支臂配出 `growth ≈ 3799.4 + (−0.4012)·launch_free`，殘差與 decode t/s 的 rank 相關是 **−0.327**
（比起跑 free 自己的 +0.275 還強）：

| 殘差 | n | t/s 中位 | p25 |
|---|---|---|---|
| < −300 MiB | 45 | 11.92 | 11.56 |
| −300..0 | 22 | 11.40 | 10.91 |
| 0..250 | 17 | 11.75 | 9.33 |
| 250..1000 | 31 | **10.68** | 10.16 |
| > 1000 | 7 | **9.82** | 7.56 |

所以設計不是「丟掉成長」，是**丟掉它的絕對值、留下殘差**。

### 9.3 新舊判準的校準與後果（同一組 122 支臂）

| 關係 | rho |
|---|---|
| growth × 起跑 free | **−0.870** |
| t/s × growth（舊判準） | −0.414 |
| t/s × 起跑 free（新閘的一半） | +0.275 |
| t/s × 殘差（新閘的另一半） | −0.327 |
| t/s × **wired 成長** | **+0.230** ⇐ 方向相反 |

`wired 成長 > 1024 MiB` 那組（n=63）中位 **11.70** t/s，≤1024 那組（n=59）中位 **10.80**：
舊碼預設拒的那一組**反而比較快**。同 tag 內去均值後：free +0.188、growth −0.323、wired +0.245（pooled n=94）。

落地後的判準：

| 判準 | 狀態 | 值 | 效果（122 支）|
|---|---|---|---|
| **起跑 `pages_free` 地板** | **on（跑前）** | 5000 MiB | 留 65/122（中位 11.84、p25 10.91）；拒的那組中位 11.03、p25 9.82、p10 7.86 |
| **成長殘差上限** | **只警告（跑後）**，`CGC_GROWTH_RESIDUAL_MB` 可選為閘 | 500 MiB | 留 100/122（中位 11.70）；拒的那組中位 10.51（預算 1000 時為 10.12） |
| 絕對成長 | 降為證據（`CGC_SWAP_BUDGET_MB` 可選回） | — | 從前它等於要求 free ≳ 8.5 GB |
| wired 成長 | 降為證據 | — | 相關 **+0.230**，擋到的那組反而快 |
| 跑中 `min_free` | 仍 off | — | 每個桶 p10..p90 都是 14–50 MiB |

### 9.4 兩個「單一讀值會抽籤」的真實缺陷

**(a) 線性 fit 在空機那一端低估。** 第一版用 `growth ≈ 3799 − 0.401·free`，它在一台 free 9191 MiB
的盒子把期望值算成 **112 MiB**——而 corpus 最空的一季（free 7323–10859）實測成長中位是 **342**。
後果在 16:21 那一輪直接看到：兩次背對背的啟動，**成長較小（550 MiB）的那次被拒，而較大（670 MiB）的那次過關**，
原因只是它的盒子比較空。改成兩段（breakpoint 由 SSE 網格搜尋定在 free 8100，交叉點 8375）之後，
期望值分別是 226 與 711 ⇒ 殘差 −41 與 +324，恢復正常單調。

**(b) 閘門的讀值與期望值的讀值不是同一個瞬間。** 16:22 那一輪：起跑閘（median-of-3）讀 **free 6520 MiB**，
而臂內 Sampler 的**單一** launch 讀值讀 **5352 MiB**——差 1.2 GB，而斜率 0.44 ⇒ 光換一個讀值就能讓期望值差 ~0.5 GB。
所以 `curve_gate` 現在接受 `launch_reading`，矩陣把 pre-flight 的 `steady()` 讀值交進去：**閘門、期望值、
與 log 印出的 free 是同一個讀值**，殘差才是一個可以重現的數。

### 9.4b 而這才是把跑後那條降為警告的原因

跑後的殘差不能用固定預算當閘，理由不是「它沒訊號」而是**它自己比預算還吵**。同一支臂、同一台盒子、
**同一小時內**的 peak 成長：`670 / 550 / 2984 / 2801 / 2614 / 1836 / 1988 MiB` ⇒ 殘差（兩段模型）
`−41 / +324 / +1244 / +1759 / +970 / +829 / +480`。把預算定在 250 或 500，結果就是**三次啟動裡有兩次被拒**，
而它們的 liveness、thermal、合約全部合格——那正是這一輪要移除的那種拒法（一個沒人能重現的數）。
所以跑後的殘差現在**一律記錄並印成 `~~ 警告`**，只有明確設了 `CGC_GROWTH_RESIDUAL_MB=<N>` 才拒。
（校準上的代價是誠實的：留 100/122、留著的中位 11.70 t/s、被拒的那組 10.51——亦即中度污染那一帶
不再被擋。要擋住它就需要一個比現在這個量更穩的量，見 §9.7。）

### 9.5 三個真實演示（同一支臂、同一台盒子、同一小時）

```
A) 預設（地板 5000）而盒子 free 3733：launch 1/3 在 spawn 之前就被拒
   state gate: UNUSABLE  free=3733 MiB (floor 5000.0)
   SESSION rc=3   啟動次數 = 0        ← 零 GPU、零秒量測

B) CGC_STATE_FREE_FLOOR_MB=3500（配合盒子實況的覆寫，記在產物裡）：放行後被跑後那一半拒
   state gate: OK  free=3732 MiB (floor 3500.0)
   啟動 47.5s ⇒ 殘差 +669（峰值成長 +2984，期望值 +2315）

C) 預設設定、盒子 free 5196：三次啟動全過，SESSION rc=0
   launch  state gate        peak 成長  殘差      時長   sample   liveness
        1     OK free 5196    2614      +970~~    52.9s  7.70462  mean_len 2.2000 drafted=81
        2     OK free 8625    1836      +829~~    48.0s  8.28717  mean_len 1.9091 drafted=100
        3     OK free 5564    1988      +480      46.9s  9.13456  mean_len 2.4667 drafted=68
   measure 147.9s + cool 60.0s（第 3 次啟動前系統自己降到 MODERATE，熱閘等了 60s）
```

**A 是這一輪的重點**：從前這道閘要在 44 秒之後才說話，現在它在 0 秒時就說「這台盒子不值得量」。
**C 是第二個重點**：同一支臂在三十分鐘前被同一道閘拒了三次（絕對成長 +1971、殘差製 +669），現在把判準
換成起跑狀態＋警告後它走完了——且三個 rep 的 draft 鏈全活（`2× returned -1`、`mean_len` 1.91–2.47）。
C 也留下了那份代價：三個 sample 是 7.70 / 8.29 / 9.13，單調上升——與本文件前面那些 session 的
（7.77 / 9.83 / 9.86）同形：**第一個 measured launch 系統性偏低**（冷啟的 OS page cache／expert cache），
而這個協定把它記下來了（逐 launch 樣本帶順序），但還沒有把它從資料裡拿掉的方法（ABBA 或明確的暖身啟動）。

### 9.6 改了哪些檔與驗證

| 檔 | |
|---|---|
| `scripts/check/memory_pressure.py` | `STATE_FREE_FLOOR_MB=5000`、兩段 `GROWTH_FIT_A/B/FLOOR`、`GROWTH_RESIDUAL_MB=500`（只警告）、`state_gate()`、`steady()`、`_launch_reading()`、`curve_gate`（起跑狀態是閘、殘差與絕對成長與 wired 降為證據或警告、`launch_reading` 參數）、`budget()` 新欄位＋三個 env 覆寫。selftest **32 → 54/54** |
| `scripts/check/llama_bench_matrix.py` | **spawn 之前**先判起跑狀態（拒則不跑、不假裝 incomplete）、`state_gate`／`state_preflight_reading` 進產物、`--dry-run` 也報起跑判詞（但不拒跑）、`launch_reading` 用 pre-flight 的 steady 讀值、警告與理由分開印、rc=4 的訊息區分兩半。selftest **+2 e2e** |
| `scripts/check/rep_split.py` | 每次啟動**前**判起跑狀態（0 秒拒、不留半支臂）、逐 launch 的 `state_gate` 證據、session 級摘要、警告與拒跑分行印、修正一行把 `growth_mb` 當判準印的舊字串。selftest **15 → 18/18** |
| `scripts/check/k3_pair_cert.sh` | 無需改（matrix 的 rc=4 照舊向上傳） |

驗證：`memory_pressure` **54/54**、`rep_split` **18/18**（含「起跑太緊 ⇒ rc 3 且**一次啟動都沒發生**」、
「waiver 放行但理由留在產物」與「跑後超標仍拒（當它被啟用）」）、`llama_bench_matrix` rc=0（含 state-gate e2e）、
`cell_contract` 30/30、`draft_liveness` rc=0、`k_swing` rc=0、`harness selftest` PASS，
加上 §9.5 的三個真實 session（A 跑前拒、B 被跑後那一半拒、C 完整通過）。

### 9.7 這一輪仍未回答的

1. **校準只有一個 cell**：122 支全是 prod-new；而 k=2/k=3 認證跑的是 **delivery**（有曲線的樣本只有 1~3 支）。
   殘差的係數對 delivery 是**外推**，所以 `budget.growth_fit.fit_from` 把出處寫進每一份產物。
2. **兩個判準都不強**（|rho| 0.27–0.41，單一 cell，混了不同 tag）。它們是「夠不夠安靜」的就緒檢查，
   不是 t/s 預測器；把「閘門過了」讀成「這支臂可以跟別人的臂比」仍然是過度解讀。
3. **起跑讀值仍然會飄**：`steady()` 把單次抽籤換成中位數，但 660 MiB 的閒置擺盪意味著
   4000–5500 MiB 這一帶的判定會隨時間而反覆。真正的解法是把盒子清乾淨到遠離地板。
4. **`swap_total` 只有 ~1 GB 餘裕時的行為沒量過**：本輪的盒子 swap 存量 4.6–6.2 GB、總量 7.2 GB，
   而閘門不看存量（rho +0.05）——「存量滿 + 成長大」這個組合可能是另一種機制，目前沒有判準。
5. `wired` 與 `min_free` 降級是因為**它們的訊號對不上方向**，不是因為它們沒訊號；要看真實因果需要別的量
   （swapins／compressor 位元組，`stamp()` 還沒採）。
6. **中度污染那一帶現在沒人擋**：殘差降為警告之後，預算 250–1000 那個桶（corpus 中位 10.60 t/s，
   比乾淨桶低 ~10%）會被放行。要把它擋回來需要一個比「跑中 swap 差」更穩的量（例如 swapins 速率、
   或 compressor 位元組的時間積分），而 `stamp()` 目前不採那三欄。
7. **低 free 桶的成因仍然只是相關**：free 小 ⇒ 成長大（rho −0.87）是機制上講得通的，但「free 小 ⇒ t/s 低」
   只有 +0.275，而兩者共享「盒子忙」這個第三因。這一輪把它當成就緒檢查，不是因果結論。
