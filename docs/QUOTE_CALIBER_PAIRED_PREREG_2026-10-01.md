# 口徑改革預註冊：`paired-v1`（引用閘門與 A4 的統計量）

> **狀態：預註冊、前瞻、未採納** —— operator 明文採納之前**不生效**；本頁與立項卡
> `scripts/check/charters/e-quote-caliber-paired-2026-10-01.yaml` 互為**單一定義**。
> 本頁所有數字都是**轉引**自樹上的既有產物（來源見 §6），**不主張**任何新讀數。

## 1. 問題（量到的，不是猜的）

判別路徑上有兩道閘門，用的都是**全 rep max/min**（極值統計）：

| 閘門 | 現在的式子 | 出處 |
|---|---|---|
| 引用閘門 R2／R3 | 全 rep 與 kept rep 都查 `max/min <= SPREAD_LIMIT(=1.10)` | `scripts/check/quote_gate.py`（1.10 的校準紀錄在檔頭：乾淨場 1.008–1.034、不乾淨場 1.207–1.941） |
| A4（判別句） | `spread_ms = max(abs(spread−1)) × max(ms_per_step)`；`|Δstep| ≤ spread_ms ⇒ REFUSE` | `scripts/check/l2010_verdict.py` |

實測（轉引，L20-10 的 §2.5.4c 孿生兩輪）：**同一支臂** n=3 → n=7，全 rep max/min
**1.0866 → 1.1197**（B 也 **1.046 → 1.0818**）⇒ 極值統計**隨 n 變嚴**，
「加 n 提升解析度」這條路與它**反向**；A4 的分母因此不是 n 能壓的。

同一輪也量到：n=3 的 **+4.8%** 在 n=7 只剩 **+1.14%**（配對 per-rep 比率中位數
**1.0114**、方向 5/7、符號檢定 **p=0.227**、t=1.23）⇒ L20-10 以**否證式結案**
（判詞維持 `REFUSE`）。⇒ 本改革的價值在**下一批格子**；⛔ **不是**用來救這一格。

## 2. 新口徑 `paired-v1`（設計）

**配對機械不必發明——樹上已有三支**（本卡的設計就是把它們串起來）：

- `scripts/check/ab_interleave.py`：交錯驅動（A,B,A,B,…，每對在同一個熱／快取窗內背靠背）；
  主量是 **per-pair 比率 `B_r/A_r` 的中位數**（檔頭原話：「drift cancels inside each pair」），
  列鍵 `<arm>#r<rep>`、每列帶 build 指紋。
- `scripts/check/rho_price_authrow.py --judge`：預註冊價格判詞的形狀 —— 兩臂**各自**先過
  `quote_gate`、效應必須大於兩臂自身散布、**兩趟反序同向**才算成立。
- `scripts/check/rho_abba_verdict.py`：**兩次序**設計（AB＝off→on、BA＝on→off；比率一律讀
  **second/first** 並記 `ratio_orientation`）＋ house precision rule：
  「配對比率只有在**大於兩臂自身的 launch-to-launch 散布**時才准帶號；**缺一個夥伴 ⇒ 散布是未知，不是 0**」。

### (a) 排程
交錯跑是**設計性質**，不是事後假設：每對背靠背；要判次序效應就跑**兩趟反序**。

### (b) 統計量
每對先算 `r_j = 第二臂/第一臂`；效應 ＝ `median r`（＋符號檢定，`alpha=0.05`，`min_pairs=8`）；
精度 ＝ house precision rule（效應必須超過兩臂自身散布）。比率方向一律按**次序表**讀。

### (c) 判決
兩臂各自先過引用閘門的**其餘**條目（R4–R8b 一字不動）⇒ 再用配對統計量判效應；
兩趟反序不同向 ⇒ `REFUSE`（次序簽名，不是效應）。

### (d) 不做的事（範圍上限）
`R4`（窗口）／`R5`（臂身分）／`R6`（輸出見證）／`R7`（profile 白名單）／`R8b`（行＝格）、
`attribution`、cell contract 全部**不動**。換掉的只有「離散／解析度」這個**統計量**。

## 3. 校準與採納（非自利條款）

採納前要**全部**成立（唯讀，不新增任何 launch）：

1. **重播 0 翻轉**：對已結語料（`Backup/**/*.json` 裡帶逐 rep 樣本與配對鍵的產物）重播新口徑，
   沒有任何 `REFUSE/DIRTY/UNSTABLE → QUOTABLE` 的翻轉（逐筆列帳）。
2. **A/A 空對照全數無號**：同一支臂、兩槽、≥ `min_pairs` 對 ⇒ 0 個號（house precision rule 生效）。
3. **負控制**：L20-10 的 n=3 與 n=7 兩對（樣本已凍結在樹上）在新口徑下**仍拒**。
   ⚠ 若任一輪變成可引用 ⇒ 這是**自利簽名**，直接否證（見 §4）。
4. **精度對 n 敏感**：合成 null 上中位數比率的散布隨 `n_pairs` 以 ≈`1/√n` 縮（容差 1.5×）。
5. **operator 明文採納** ＋ 測試卡宣告 `caliber: paired-v1`；**前瞻生效日**寫進立項卡
   —— 該日之後立卡的格子才適用；**舊判詞 0 改動**。

## 4. 否證（任一成立即退回原口徑）

1. 重播出現任何 `REFUSE/DIRTY/UNSTABLE → QUOTABLE` 的翻轉；
2. A/A 空對照出現號；
3. **L20-10 的任一輪**在新口徑下變成可引用（自利簽名）；
4. 配對鍵在語料上不可得、且無法用一趟宣告過的 `ab_interleave` 交錯跑補出（配對只存在於假設）；
5. 精度對 `n_pairs` 不敏感（那表示統計量其實還是極值）。

退回即維持 A4 與 R2/R3 原樣 —— `quote_gate.py` 檔頭那句照舊生效：
**「不能因為某個好數字被擋而放寬」**。

## 5. 未決（UNRESOLVED，不是否證）

可配對樣本 `< min_pairs`，或產物缺序資料（臂序／rep 序）⇒ 標 `UNPAIRED` 並登記 UNRESOLVED：
先補一趟**宣告 caliber** 的交錯跑再重播；**不得**把「沒量到」唸成「量到 0」。

## 6. 帳（單一定義 —— 誰在哪裡）

| 角色 | 位置 |
|---|---|
| 立項卡（判決規則本體） | `scripts/check/charters/e-quote-caliber-paired-2026-10-01.yaml` |
| 現成配對機械 | `scripts/check/ab_interleave.py`、`scripts/check/rho_abba_verdict.py`、`scripts/check/rho_price_authrow.py --judge` |
| 閘門本體（要改的兩處） | `scripts/check/quote_gate.py`（R2/R3、`SPREAD_LIMIT`）、`scripts/check/l2010_verdict.py`（A4） |
| 統計量＋翻轉帳（**已交付**：唯讀，閘門 0 改動） | `scripts/check/caliber_paired.py`（`--selftest` 31/31；全樹帳 `Backup/caliber_paired_2026-10-01/audit.json` ＋ `audit.md`） |
| 來源判詞與診斷 | `Backup/l2010_b1b3_2026-09-30/clean_ws192_verdict.json`（含 `diagnostic`） |
| 結案文件（為什麼不是救這一格） | `docs/L2010_CLEAN_WS192_20261001.md`（結案（operator 明文）段） |
