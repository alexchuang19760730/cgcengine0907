# premise B 的交付口徑讀數（2026-09-28）—— 主 context `ntok=1` 的 churn

> 立項：`scripts/check/charters/exp-churn-delivery-630.yaml`（`owner=lineA`，axis `S`）
> 產物：runA `Backup/churn_delivery_630_2026-09-28/churn.json`（23:06）
> 　　　runB `Backup/churn_delivery_630_2026-09-28/runB_churn.json`（23:11，複製品）
> 　　　同目錄兩份 `.stderr.log`（runB 帶 `runB_` 前綴）；`Backup/exp_runs/exp-churn-delivery-630_20260928_231234.json`（runB 的 sync 副本）
> build `engine_build e5d1c0f14` / **630**

## 1. 一句話

**交付步（MTP off、主 context、`ntok=1`）的 churn 不是 0。兩筆獨立 run：publish 級 19.8–20.2%，
entry 級 3.2–3.4%。** ⇒「逐步之間那張表是常數」在**今天的口徑上也不成立**。

這是全專案**第一次**在交付形狀上量到那一格。樹上既有的 `ntok=1` 讀數（`0/612`）是
**draft** context（09-20 三次 MTP-on run），不是交付步；這兩輪的 log 都沒有 draft 行，
讀取器歸屬為 `main`（`delivery_ntok1_measured = True`）。

## 2. 讀數（兩筆，同 cell 同臂）

| | runA 23:06 | runB 23:11（複製） |
|---|---|---|
| publish 級 churn | **20.2%**（3026/14976） | **19.8%**（2958/14976） |
| entry 級 churn | 3.4%（4014/119808） | 3.2%（3822/119808） |
| `consumed_unchanged_publishes` | 11950 | 12018 |
| 整表 `changed_entries` / `publishes` | 8092 / 15015 | 7719 / 15015 |
| coverage | 99.7% | 99.7% |
| 品質閘 | `clamped_selected=0`、`zero_mapped=0` | 同（`clamped_table` 1696695 → 1692497） |
| thermal | NOMINAL（125 樣本） | NOMINAL（tg window 38/38） |
| 起跑 `pages_available` | 6433 MiB | **8523 MiB** |
| swap 成長／峰值 | +3878 / 8075 MiB | **+1579 / 7543 MiB** |
| attribution | swap | swap |
| `tg`（**都不得引用**） | 9.07 ± 0.42 | 9.94 |

散布證據（立項卡 ②，不得只報純量）：385 graphs × 40 layers，逐 (graph, layer) `SLOT-SEL` 行
15400 筆，`wrong=0`、`unowned=0` 全綠；「有動到的 layer-step」78/384（runA）與 76/384（runB）。

兩個粒度必須一起寫：只寫 20.2% 會讓「有一步在動」看起來像「多數 id 在動」，
而 entry 級只有 3.2–3.4%。**publish 級＝「這一步髒了沒」，entry 級＝「髒了多少」。**

## 3. 口徑（這一格之前不存在，所以記全）

* 入口：`harness bench`（認可入口）、profile `prod-new`、**cell `delivery`**
  （`-b/-ub 512`、`p=0`、`n=128`、`d=512`、`r=3`、`warm-skip 64`、`ctx 4096`、`fixed_fill_seed 0`）
  ⇒ **cell 口徑校驗通過（嚴格維度 15 項一致）**。
* 臂：`{CGC_S1_TABLE_CHURN=1, CGC_SLOT_TABLE_GPU=1, CGC_GPU_TIMING=1, CGC_DECODE_PROFILE=1}`
  —— 都是**已進白名單**的旋鈕（`CGC_S1_TABLE_CHURN` 09-16），且 **`spec_type=""` ⇒ MTP off**。
* 量測期 thermal：兩輪都 **NOMINAL**。
* 起跑閘：兩輪都壓縮機 **0.00 MiB/s**（runB 是連續三次取樣確認後才起跑）。

## 4. 記憶體混淆：複製品把它從「假設」變成「已排除」

runA 是重壓下量的（+3878 MiB、page-in 16.2k/s、swapfile 5120→9216），
所以第一版結論把 20.2% 標成「**逆風偏上估**」。複製品就是為了檢驗這個。

* runB 的成長只有 runA 的 **41%**（1579 vs 3878），起跑 `available` 高 **2090 MiB**（8523 vs 6433）。
* 若記憶體壓力真的在製造 churn，runB 應該明顯更低。**實測：20.2% → 19.8%，entry 3.4% → 3.2%。**
  ⇒ 壓力假設**被這個對比否證**：這兩個量之間只差 0.4 pt（相對 2%），遠小於兩輪條件的差別。

⇒ 讀數升級為 **19.8–20.2% publish／3.2–3.4% entry**，且「壓力不是驅動項」現在是**證據**，不是信念。
（仍然不得把它當常數引用：這是**兩筆**、同一天、同一台盒子。）

**兩個標籤仍然得帶著走**：

* `attribution.verdict = swap` 在兩輪**都成立**（`SWAP_GROWTH_MB=512` 這條：
  13.6 GB 模型 + 8.6 GB pool 在這個盒子上的載入期成長就超過它）。
  ⇒ 它作廢的是**同次的 `tg`**（9.07／9.94 永不引用），不是計數器。
* 反過來，**起跑視窗的條件 runB 是滿足的**（壓縮機安靜、`available ≥ 8000`），
  所以「這一輪是在盒子最乾淨時量的」與「這一輪的吞吐仍不可引用」**同時為真** ——
  兩件事不能互相推論，這正是 §5.0 口徑裁決要分開寫的原因。

## 5. 裁決（S3）

立項卡的分支寫死了：`0 ⇒ S3 復活`、`>0 ⇒ 22.2 那條路在今日口徑上也關閉、力氣轉 shape/IO`。

兩輪都落在 **`>0`**：

* premise B（「池在一步之內凍結、發布冗餘」）**在 MTP-off 的交付圖上也不成立**；
* 22.2 t/s 的單段路徑**維持關閉**，但它現在的封條是**自己量到的 19.8–20.2%**，
  而不是從 `ntok=4`／MTP-on 的 42.3% 推論來的 —— 論證鏈少了一跳；
* 依立項卡 `on_fail`：**不要**因為這個結果重開 S3；decode 20 的力氣回到 shape／IO
  （天花板 20.49 / 16.31）。

## 6. 這一輪順手抓到的三個製程缺陷（都不是本實驗的結論，但會影響下一輪）

1. **`--cell <name>` 不會把測試卡 §2.5 的維度套進 llama-bench**。dry-run 顯示
   只給 `--cell delivery` 會拼出 `-p 0 -n 128 -d 0,512,1024,2048,4096`（default 的 depths）
   ⇒ 被 cell contract 拒跑，訊息正確（`depths`、`warm_skip` 兩條）。
   少這一步的人會以為「cell 名稱＝口徑」，實際上還得自己對齊 `--depths 512 --warm-skip 64`。
   （好消息：這個錯誤**當場**被擋掉，不會產出一個別格子的數字。）
2. **兩個閘對同一台盒子給出相反答案**：23:06 那一分鐘 `server_window.py status` 報
   `BUSY: reclaimable=6009MB<8000; compressor busy 4.47`，而 `harness bench` **照跑**
   （它的起跑閘只看壓縮機流量，那個當下是 0.00）。
   ⇒ 8000 那道「reclaimable」條**還在判 BUSY，卻已經不是拒跑的閘**（09-28 起壓縮機流量才是）。
   同一個 `status` 指令在 runB 前也報 BUSY（7583MB），而兩輪都真的跑了。
3. **8 秒的流量窗會把剛收工的盒子判成忙**：第一次起跑被 `compressions 159.46 MiB/s` 拒掉，
   15 秒後同一支探針連續 5 次讀 `0.00`。拒跑是 fail-closed ⇒ 安全，但**代價是一次起跑機會**，
   而且它拒絕的理由（別條線剛 teardown 的壓縮尾巴）與盒子當下的量測品質無關。

## 7. 還沒做 / 未解

* **低壓力的第三筆**：兩輪的 `attribution` 都是 `swap`，而樹上 `delivery` cell 的 `none` 判決
  只存在於 **anchor 臂**（`Backup/anchor_repro_2026-09-28/r3*.json`，profile `prod25`、
  `!CGC_PREFILL_STREAM=1;!CGC_GATHER_SLAB_CAP=256`），**沒有** churn 臂的低壓力樣本。
  要拿到 `attribution=none`，需要 `swap_growth ≤ 512` **且** 峰值 ≤ 6144 MiB；
  這個臂的成長（3878 / 1579）目前都超過 ⇒ **可行但未證**。若它其實結構上不可達，
  那要改的是判準本身（counter 讀數不該由吞吐歸因判決），不是放寬那個布林。
* `n_fast_cold` 在 MTP-on 交付形狀上的對照讀數（兩輪皆 N/A：MTP off 沒有 fast path 行）。
* 台帳：本輪未在 `docs/DIAGNOSTIC_ARMS_LEDGER_*` 登記臂列
  （`arm_ledger_check` 自陳不涵蓋「漏登記」）；登記表 `claim_instruments.yaml`
  與 mindmap 節點 `exp-churn-delivery-630` 都已更新。

## 8. 本輪產出的機器檢查：`mindmap_void_check.py`

**這一輪跑完之後，決策面上多了一個 VOID 數字** —— 這是本節存在的理由，不是附註。

`experiment_sync` 把產物寫進 mindmap 節點後，節點變成：

```
res                    = "decode 9.94 t/s（swap）"      <-- swap 歸因，依 §5.1 不得引用
best                   = {metric: tg, tg: 9.94, ...}
target_gap.decode_tps  = {current: 9.94, target: 20.0, gap: -10.06, pct: 49.7}
```

⇒ 一個不可引用的吞吐數字同時變成節點的「成績」**與「距目標 49.7%」**。
`void_number_check.py` 抓不到它：那支只掃 `docs/*.md` 的決策面（14 檔），
而 mindmap 是**渲染面**（`index.html` ＋ 53 份 `briefs/*.md`）。

新增 `scripts/check/mindmap_void_check.py`（`--self-test` **16/16 PASS**）：

* 對每個節點收三處吞吐主張：`res` 的 `N t/s`、`best.{tg,pp}`、`target_gap.*.current`；
* 查出處：`best.log` 或 `runs[].log`（repo 相對產物），用 `best.arm` 的 tag 挑臂，
  讀 `attribution.verdict`；**乾淨的定義沿用 `memory_pressure.attribute()` 唯一的乾淨標籤 `none`**
  （`swap`／`both`／`thermal`／`contention` 都不乾淨）；
* 缺席與不明一律 fail-closed：沒有 `attribution` 欄位、產物讀不到、多臂又對不上 tag
  全部不算乾淨（`UNPROVEN`），跟 `VOID-UNBACKED`（有主張但節點沒有產物）分開列。
* 階梯：`VOID-IN-RESULT`／`VOID-UNBACKED`／`UNPROVEN`／`EXEMPT`（節點宣告
  `no_throughput_claim` 或 `--allow <id>` 點名，**會列印**）／`CLEAN`／`N/A`。

真樹（2026-09-28 23:2x）：**4 個節點不合規 / 53 已檢**，rc=1（設計如此，跟
`claim_instrument_check` 同一個慣例：報告，不是回歸）：

| 判定 | 節點 | 讀數 | 出處判定 |
|---|---|---|---|
| `VOID-IN-RESULT` | `exp-s-retro` | `decode 11.97 t/s（swap）` | `cbnmain_pair/{on1,off2}` 都 `both`（thermal=HEAVY） |
| `VOID-UNBACKED` | `cache-prebind` | `14.33 t/s（+14.0%）` | 節點沒有任何產物 |
| `VOID-UNBACKED` | `cache-rho` | `14.36 t/s（+14.2%）` | 節點沒有任何產物 |
| `VOID-UNBACKED` | `s1-segbatch` | `A 11.30 → B 20.73 t/s` | 節點沒有任何產物 |

本節點現在是 `N/A` —— `res` 已改成不主張吞吐（3026/14976 這種分數形式，見下）。

**同族陷阱（一併記下）**：`mindmap_brief_build.big_number()` 用 regex 從 `res` 抓大數字
（`t/s` ＞ `×` ＞ `%`），所以在 `res` 寫「19.8–20.2%」會在其他節點放 **t/s 的同一個視覺位置**
印出 `19.8%`，讀起來像「19.8 / 20」。因此本節點改用**不含 `t/s`／`×`／`%`** 的分數形式。

**交接（我沒動那兩個檔，它們正被另一條線改動中）**：

* `scripts/check/experiment_sync.py` 的 `merge_run()`（`:179` 無條件用 tg 寫 `res`）與
  `compute_best()`（挑最高 tg，不看歸因）—— 這兩個是**產**這個漏的地方；
  修它需要在該檔加「非乾淨歸因不得更新 res／best」的判準，而該檔現在是 dirty。
* `scripts/check/provenance_gate.py` 同樣 dirty（另一條線加了 `formula_audit.py` 的
  `REPORT_EXEMPT` 一條，4 行）。本檢查器**不寫 `docs/*.html`**，所以不需要進那個登記表；
  `provenance_gate check` 對本輪的產物仍 **PASS**（offenders 40 vs baseline 41，無新增），
  `--selftest` **74/74**。

### 8.1 首次實用：4 → 0（結清那四個節點）

檢查器一上線就抓到 4 個，而**每一個都查不到可補的乾淨出處**（不是懶得補）：

| 節點 | 舊 res | 查證結果 | 現在的 res |
|---|---|---|---|
| `s1-segbatch` | `A 11.30 → B 20.73 t/s` | 09-24 matrix 直跑（`CGC_SEG_BATCH` 09-25 才進白名單）；該目錄的 A/B 是 12.20/11.96/9.32/11.71 → 22.22/20.30/20.06/20.33，**舊 res 那兩個數在該目錄找不到**；B 臂 `attribution=thermal`（HEAVY、`max_llama_procs=3`） | 判詞：A/B 兩端都作廢 |
| `cache-rho` | `14.36 t/s（+14.2%）` | 是**定價換算**（cb→ms/步→t/s）；來源文件自陳「本輪 t/s 不可引用」；`Backup/` 無產物 | 判活，不主張吞吐 |
| `cache-prebind` | `14.33 t/s（+14.0%）` | `Backup/` 無產物；由 `prebind_probe_run.sh`（`CGC_PREBIND_*`）驅動，而 **launcher 白名單裡沒有任何 `CGC_PREBIND*`** | 判活，不主張吞吐 |
| `exp-s-retro` | `decode 11.97 t/s（swap）` | `cbnmain_pair/` 的 **9 個 run 沒有一個乾淨**（both/HEAVY ×3、swap ×5、thermal/HEAVY ×1）；11.97 出自 on3（swap） | 判詞：該族作廢 |

做法：把 `res` 改成**不含 `t/s`／`×`／`%`** 的判詞（避開 `big_number()` 的放大位），並在節點加
`no_throughput_claim`——它存的是**為什麼不能放吞吐**（入口、出處、歸因、正確性），由檢查器逐條列印。
`exp-s-retro` 的 `note`（假設欄）裡的裸 `20.73 t/s` 也一併標上「該宣稱的兩端皆已作廢」。

結果：`mindmap_void_check` = **0 節點不合規 / 53 已檢（N/A=49、EXEMPT=4，CLEAN=0），rc=0**；
渲染面上那四個節點的放大數字位與目標達成欄都已空掉。
⚠ 殘餘：`EXEMPT` 的語意是「本節點不把吞吐放上成績面（含：原本那個已作廢）」，不是「這個數字被證明乾淨」；
若哪天那些路線要用認可入口重量，應該**新開節點**而不是解除這個標記。

### 8.2 寫入端：讓那個錯誤產生不出來

§8 的檢查器是**事後**的。同一條規則現在也在**產它的地方**擋一次：

* **唯一定義**：`memory_pressure.is_clean()`（乾淨 = `attribution.verdict == "none"` 且 worst thermal
  NOMINAL；缺席／空 verdict 一律不乾淨）。寫入端 `experiment_sync` 與檢查器 `mindmap_void_check`
  都呼叫它，檢查器會在報告裡印「用的是哪一份」。
* `build_run` 把 `clean`／`void_why` 標在每條 run 上；`merge_run` 改成呼叫 `_recompute_result()`，
  從**整個** run set 重算 `res`／`best`／`target_gap` —— 只有乾淨的 run 能把吞吐放上成績面。
* 一個乾淨的都沒有時：`res` 改成「尚無可引用讀數：N 個 run 全部非乾淨（…）」**且只在這兩種情況下改**——
  它現在是吞吐主張，或者它還是 `build_entry` 的預設句。**人寫的判詞不動**（用機器句子蓋掉人寫的判詞
  也是一種資訊損失），只補上機器的 `no_throughput_claim`。後來有乾淨的 run ⇒ 恢復讀數並拿掉標記。
* 去重那一條路**也會重算** ⇒ 本規則之前寫壞的節點會自己收斂（而不是永遠停在壞狀態）。

自我測：`experiment_sync --selftest` 新增 10 條（含「swap 的 9.94 不得再變成 `decode 9.94 t/s（swap）`」
與「49.7% 那條路」的形狀）、`memory_pressure --selftest` **65/65**（新增 5 條 is_clean）、
檢查器 **16/16**。端到端：把本實驗那兩筆 swap 的產物重 sync ⇒ 節點自己變成誠實狀態
（`best: null`、`target_gap` 全 None、人寫的判詞 res 保留、機器種下 `no_throughput_claim`）。

順手修掉一個**被我弄紅的既有自測**：`harness.py selftest` 的「every launcher has a window stance」
一直是紅的，因為 audit 用**文字比對**把 `claim_instrument_check.py` 認成 launcher
（它只是列了執行檔路徑與啟動檔名當資料）。已為它與 `mindmap_void_check.py` 宣告 `needs_window=False`。
⚠ 那個比對**連註解都看**：第一版註解裡照抄了啟動形狀，結果把 `harness.py` 自己變成了 launcher ——
現在的註解刻意不寫那個形狀。

### 8.3 第二個寫入端：排行榜（檢查器本來看不到它）

`score-leaderboard` 的 `res` 是「decode 最高 N」——**一個 t/s 主張**，但它既沒有 `runs` 也沒有
`best.log`，而且 res 沒帶單位 ⇒ 檢查器原本把它當成「不主張吞吐」而**靜默放行**。
它正好是那種「把全語料最大值寫成成績」的載體：修正前的榜首是 `prod25-stream`（MTP on）的 **13.099**
與 seg-batch 家族的 **374.3**。

檢查器補上（現 **18 個案例全過**）：① `leaderboard` 欄位就是一則吞吐主張；② 出處 = res 報的那一桶
（steady 優先於 all）的榜首 `src`；③ **所有**主張的出處都要乾淨（不是「有一個乾淨就算過」）。

寫入端也收緊：`build_leaderboard` 只讓乾淨歸因的臂上榜（`_arm_on_board`，可單獨測），並回傳
**掃描統計**（不是靜默丟掉）；`res` 補上單位與**口徑旗標**；順手修掉一個會自我增長的缺陷 ——
掃描的 glob 包含 `Backup/**`，而 `persist_leader_checks` 把 /tmp 來源複製進 `Backup/leaderboard/`，
所以**榜在掃自己的輸出**（同一筆成績重複上榜並一輪一輪累積）。現在排除那個目錄，連跑兩次結果一致。

結果（2026-09-28 23:3x）：**734 個臂只有 83 個乾淨**（跳過：沒有 attribution 405、both 83、
swap 132、thermal 16、contention 5、`none／thermal=MODERATE` 10 —— 最後一類是 `none` 但 thermal 不是
NOMINAL，理由現在寫在統計裡）；榜單從 13.099（prod25／MTP on）變成
**decode 17.669 t/s（prod25；非 §5.0 認可口徑）／prefill 306.678 t/s**。

⚠ 兩個交出給 operator 的問題（我沒有自己決定）：

1. **`decode/steady` 現在是 0 筆**。過濾後沒有一個「穩態形狀且乾淨」的 decode 臂 ——
   也就是語料庫裡**沒有可引用的生產 decode 讀數**。節點現在把這件事說出來（而不是用 13.099 遮著）。
2. **`all` 榜的榜首是 prod25／MTP on**。歸因乾淨 ≠ 口徑認可：依 §5.0，那個數字仍然不是「我們的 decode」。
   我已把口徑寫進 `res`，但「榜要不要只收 prod-new」是口徑決策，不是歸因決策。

殘餘（已知、未修）：同一筆量測會出現兩次（in-repo 原件 ＋ persist 下來的檢驗檔）；
兩者 tag 相同、數值相同，是內容級去重，不在本次的守門範圍。

**邊界**：這道守門只覆蓋 `experiment_sync` 的寫入路徑；用別的方式進到節點的數字仍要靠 §8 的檢查器。
