# 口徑統一：認證入口的**單一定義**（12+ 直認證）— 預註冊 2026-10-01

> **性質**：本檔是規則，不是報告。跑前寫死；下方「§6 驗收」的每一條都要在這一輪跑完並回填。
> 相關：`scripts/check/charters/e-caliber-unified-2026-10-01.yaml`（立項卡，與本檔互為單一定義）、
> `docs/S3B_RHO_COST_2026-09-30.md §8`（R8 的來源與 12+ 的定位）、
> `scripts/check/quote_gate.py`（判準本體）、`scripts/check/caliber_gate.py`（口徑三條）、
> `scripts/check/cell_contract.py`（格子的單一定義）。

## 0. 一句話

operator 2026-10-01：**「量測口徑一定要完全統一，這樣過 12+ 才可以直接認證並放在已認證區。」**
⇒ 本輪把「可以入認證表」寫成一個**只有一份定義**的判詞，並做成一支持出來就能用的認證入口；
此後任何一個讀數只要**過 12+ 且通過那同一條鏈**，入表不需要再逐件裁定。

operator 同日追加（本節的最終規矩）：**「certify <產物> --target-ts 12 應該是要比現在的已認證高才進去；
同時更新現在的看板 11.703 …… 這已經是舊的了」** ⇒ 門檻不是固定的 12，而是**棘輪**：
入表要**嚴格高於現行已認證上限**（單一來源＝看板 `certify_anchor.decode_ts`；現值 **11.982966**），
`--target-ts 12` 只是**可選的下限**；上限讀不到 ⇒ 全部 `REFUSED`（fail-closed）。

## 1. 現況（為什麼「12+ 還不能直接認證」）

12+ 那一族（`Backup/s3b_abba_2026-09-30/*`、`Backup/s3b_abba_2026-09-30/rho_*`）今天的引用狀態是
`quote_gate` R8c 判 **REFUSE**：它是 `ab_interleave.py` 的自訂**輪級聚合**（`n_rounds=3`＋`warmup=1`、
n≈98、只有 `rounds[]`，沒有 `samples_ts`、沒有權威格）。就算把輪中位 repack 成 row，n≈98 也不是任何
已宣告格的行 ⇒ R8b `DIRTY`。（機械判詞：`docs/S3B_RHO_COST_2026-09-30.md §8.2`。）

另外兩個**已知不統一**的角落（本輪要關掉其中一個、另一個只列帳）：

1. **三層包裝**（`{plan, records:[{rows:[…]}]}`，例如 `Backup/seg_batch_s1_pairs/abba_212809.json`）
   **不在** `quote_gate.iter_rows` 的走訪範圍 ⇒ 綁它的 doc 主張仍是 `NA`（列出、不判違規）。
   §8.4 自己寫下「下一個硬化步驟＝讓 `iter_rows` 也走 `records[].rows`；屬 operator 決定」⇒
   operator 已下令「完全統一」，本輪關閉。
2. **producer 側未進口徑的 bench 讀數**：`prod_profile.py` 寫自己的 schema
   （`axes[].records[].rows[].t/s`，沒有 `avg_ts`／`cell`／`contract`／`profile`／`attribution`）
   ⇒ `quote_gate` 讀不到它、`caliber_gate` 只能靠字串命中。歷史上 12.57／13.10 的交付讀數
   就是這條路徑產的。**它的正確處置不是加一份相容讀取器**（那會變成第二份定義），而是
   要認證就**走統一入口重跑**：`harness bench`，命令由 `cell_contract.py --cell <name>` 印出。

## 2. 統一定義（唯一；本輪之後所有「可不可以入表」都只讀這一條）

一個讀數可以寫進認證表，**當且僅當**下列四條同時成立（缺一即 `REFUSED`，不得以散文補）：

| # | 條 | 唯一實作 | 讀不到時的失敗方向 |
|---|---|---|---|
| 1 | **口徑三條**：入口＝harness bench（llama-bench 的 cell row）、格子∈測試卡 §2.5 宣告、聚合＝逐 rep 向量 | `caliber_gate.classify()` == `UNIFIED` | 判不了 ⇒ `REFUSED` |
| 2 | **引用四∼八**：R1–R8（reps≥3；全 rep 與 kept 的 max/min ≤1.10；`attribution=none`；臂上無 R5／R6；`profile∈{prod-new}`；同格／同行／同聚合） | `quote_gate.judge()` == `QUOTABLE`（**目標 row** 逐列判） | 判不了 ⇒ `REFUSED` |
| 3 | **棘輪**：目標 row 的平均值**嚴格高於現行已認證上限**（decode；＝看板 `certify_anchor.decode_ts`，現值 **11.982966**）；下限另選配（`--target-ts 12` ⇒ decode ≥ **12.0**）；prefill ≥ 250.0 | `certify_anchor`（單一來源）＋ `--target-ts` | 上限讀不到 ⇒ `REFUSED`（fail-closed）；`--target-ts` 預設 0（＝只套棘輪） |
| 4 | **格子今天仍在**：格名 ∈ 現行 `cell_contract.cell_names()`（宣告被改名／拿掉 ⇒ 落 3） | `cell_contract.declared_cells()` | 讀不到格子清單 ⇒ `REFUSED`（fail-closed） |

⚠ 棘輪**唯一的例外**：判的檔就是 `certify_anchor.artifact` 那件產物（**紀錄本人**）時，以
`avg ≥ 上限 − 1e-4` 放行 —— 否則「拿紀錄本人的產物重驗一次」必然失敗；其他任何檔一律嚴格 `>`。
平手也不算高過（差 < 1e-9 視為同一件讀數）。已入表的舊件（如 C3 的 11.703）重驗會得到
「沒有高過現行上限 ⇒ REFUSED」——那不是「它當時沒過」，是它現在已經在上限之下。

**「直接認證」的意思**：四條是**跑前寫死的常數與既有的單一定義**，通過即等於認證；
不需要 operator 逐件點頭、不需要任何欄位以外的批准。入表的列＝工具印出的
`certified:` YAML 片段（id 由人給），而列上寫的 `quote.verdict` 由看板 D7 **每次建板當場重判**。

**明示邊界（本輪不做）**：

- ⛔ **不**為 server-round（n≈98）宣告新格、**不**擴充 `cell_contract` 去描述 round 口徑
  —— 那條（§8 的 (b)）仍是 operator 的另一個決定，本輪不動。
- ⛔ **不**改 C1–C4／C6 的既有列、**不**重判任何既有判詞（dated 產物不回改）。
  （10-01 追記：**新增** C7／C8 兩列、C3 只加一句「已被棘輪取代」的註記（判詞未動）、
  看板加 `certify_anchor` 並把 summary 的 11.703 換成 11.9830。）
- ⛔ **不**放寬 R1–R8 的任何常數（1.10 仍是 1.10；解鎖走 paired-v1 的預註冊卡）。

## 3. 認證入口（新工具；判準不新寫一行）

```
python3 scripts/check/caliber_certify.py certify Backup/.../x.json            # 判一個產物（所有 row）
python3 scripts/check/caliber_certify.py certify x.json --target-ts 12 --kind decode
python3 scripts/check/caliber_certify.py certify x.json --board-row          # 加印 certified: YAML 片段
python3 scripts/check/caliber_certify.py audit 'Backup/**/*.json'            # 全樹帳：誰可認證、誰差在哪
python3 scripts/check/caliber_certify.py --selftest
```

規則：工具**只呼叫** `caliber_gate`／`quote_gate`／`cell_contract` 三份既有定義；
自己不重寫任何一條。`CERTIFIED` 的 rc=0、`REFUSED` 的 rc=1；任何「判不了」都是 `REFUSED`。

## 4. 硬化（關掉角落 1）

`quote_gate.iter_rows` 增加走訪 `records[].rows`（三層包裝、一層，寫進 R8 的檔頭）。
判詞的臂身分以**檔級欄位與 record 的合併視圖**判斷（record 的 `tag`／`env` 優先），
檔級 `cell` 保留。⚠ 這條**會把既有 NA 變成可判** ⇒ 逐筆列帳（§6），若翻紅，修的是**主張的寫法**
（標非主張或進凍結帳），不是判準。

## 5. 判詞（照 §2 映射；跑完回填）

- 任一既有的「已認證」列（C1–C4／C6）在新範圍下被判到且翻紅 ⇒ **本輪失敗**、回退硬化。
- `audit` 的帳：`CERTIFIED`（＝現在真的進得去的）／`QUOTABLE_NOT_ABOVE_ANCHOR`／`REFUSED(理由)`／
  `NOT_JUDGEABLE(schema)` 逐類計數；12+ 那族若仍 `REFUSED`，理由必須逐字是 R8c/R8b（不是「大概不行」）。
- 前一階段的 paired-v1 翻轉帳維持 **0 翻轉**（`Backup/caliber_paired_2026-10-01/audit.json`）。

## 6. 驗收（跑前寫死；本輪要跑完並回填）

1. `caliber_certify.py --selftest` 全 PASS，且**含負控制**：髒窗（`attribution≠none`）、
   臂上帶 R5／R6、離散 >1.10、reps<3、格子未宣告、輪級聚合、**棘輪沒過（≤現行上限）**、
   門檻未達、**棘輪讀不到（看板缺鍵／缺檔 ⇒ fail-closed）** —— 每一種都要 `REFUSED`；
   正向另含**紀錄本人**（判的檔＝`certify_anchor.artifact`、avg＝上限 ⇒ `CERTIFIED`）。
2. `quote_gate.py --selftest` 全 PASS（新增三層包裝的判詞案例後仍全綠）。
3. `caliber_gate.py --selftest` 全 PASS。
4. `doc_claim_gate.py --selftest` 全 PASS；實跑（交付面）在**修完翻紅的主張**後 rc=0；
   同時 `--all-docs`（歸檔面）只稽核、不影響 rc，翻紅筆數逐筆列帳。
5. `decode_board_build.py --selftest` 與 `--check` 全 PASS（唯一看板來源不許漂）。
6. `landing_ledger.py` 16/16 落地不變。
7. `audit 'Backup/**/*.json'` 產出帳（工具內建、不寫檔；要留檔用 `--json`）。

## 6.1 結果（2026-10-01 回填；照 §6 逐條對）

| # | 項 | 結果 |
|---|---|---|
| 1 | `caliber_certify.py --selftest` | **34/34 PASS**（10-01 14:0x：棘輪定稿 → D17／`--update-board` 後重跑）—— 乾淨 12+ ⇒ CERTIFIED；`attribution=swap`／`CGC_RHO_PROBE`（R5）／`CGC_SEG_BATCH`（R6）／離散 1.107／reps=2／未宣告格／輪級聚合／外來 schema／stddev 不自洽／**棘輪沒過（11.2 ≤ 11.5）**／**棘輪讀不到 ⇒ fail-closed（缺檔、缺鍵各一）** ⇒ 全部 REFUSED；`--kind` 過濾（只有 prefill 達標時 decode 不認證）；**紀錄本人**（avg＝上限）⇒ CERTIFIED、同值但非本人 ⇒ REFUSED；另新增 **`--board-row` 片段**（`ratchet` 欄位、紀錄本人哨兵、prefill 型的 `ratchet_exempt`）與 **`--update-board`**（推升／回滾／不推／prefill-best 六類） |
| 2 | `quote_gate.py --selftest` | **31/31 PASS**（新增三層包裝兩案例：缺逐 rep ⇒ `REFUSE`、完整 row ⇒ `QUOTABLE`；原 30 條一條未動） |
| 3 | `caliber_gate.py --selftest` | **6/6 PASS** |
| 4 | `doc_claim_gate.py` | selftest **46/46 PASS**；交付面（11 檔）**rc=0、綁定違規 0**。硬化後唯一的翻紅是 `docs/FILL_TERM_DELIVERY_2026-09-30.md:63`（`20.73` 其實是 B 臂**四列的中位數**，不是任何一列的讀數）⇒ 修的是**主張的寫法**（標「不可引用」＋兩端作廢），判準一條未動。歸檔面（488 檔，只列不判）：綁定違規 **5**、未登記無主讀數 **102**（全在 09-25 以前的 dated 產物，依慣例不回改） |
| 5 | 看板 | `decode_board_build.py --selftest` **99/99**（＋D17 八案例）、`--check` **PASS（0 個問題）**（含 **D17 棘輪 vs 認證表**）。另：L20-7 的舊卡點（probe 閘字串）因**切分落地**轉 `GONE` ⇒ 本輪把該格照機器的要求改成 `needed／verdict_file`（兩個獨立根：`verdict.json` ＋ 預註冊文件的 `## 判決：` 節），決策佇列由 4 格降為 3 格 |
| 6 | `landing_ledger.py` | **16/16 落地（結案 12）** —— L20-7 由 `blocked／code`（補丁套不上）轉 `needed／verdict_file` |
| 7 | `caliber_certify.py audit 'Backup/**/*.json'` | （a）目標制（舊）：2769 檔：**CERTIFIED 3**（都是 prefill ≥250：302.3／305.4／309.7）、**QUOTABLE_BELOW_TARGET 6**（decode 9.377–11.98）、REFUSED 893、NOT_JUDGEABLE 1822、UNREADABLE 45 —— 帳：`Backup/caliber_unified_2026-10-01/audit.json`。<br>（b）**棘輪制（10-01 最終、重跑）**：2770 檔：**CERTIFIED 4**（C8 的 archive decode 11.9830 ＋ 三筆 prefill）、**QUOTABLE_NOT_ABOVE_ANCHOR 5**（C7 的 11.9483／A 臂 11.63／launch3 10.75 等）、REFUSED 893、NOT_JUDGEABLE 1823、UNREADABLE 45 —— 帳：`Backup/caliber_unified_2026-10-01/audit_ratchet.json`。⚠ 換棘輪後「CERTIFIED」的語意是**現在進得去的**（上限之下的舊件自然落在第二桶），不是「已在表上的」。 |

**這一輪量到的關鍵事實**：全語料**可引用 decode 的天花板是 11.9830 t/s**（`Backup/l2010_b1b3_2026-09-30/clean_logs/archive/Q3.20261001-071453/clean_ws192_B.json`，n=3；
n=7 孿生 `Backup/l2010_b1b3_2026-09-30/clean_ws192_B.json` ＝ 11.9483）——
也就是說「過 12+」今天**一個都沒有**。這正是這條入口的意義：**現在不是「12+ 被口徑擋住」，是「還沒量到 12+」**；
一旦量到（`harness bench`、宣告格、逐 rep、乾淨窗、臂上無 R5／R6），它會直接認證並吐出入表片段，
不再需要逐件裁定。12+ 那一族（server 輪級聚合）仍逐字判 `REFUSE`（R8c），理由在認證入口的輸出裡。

## 7. 交付物

- `scripts/check/caliber_certify.py`（認證入口；本輪新增＋棘輪定稿）
- 看板 `scripts/check/decode_board_2026-09-29.yaml`：`certify_anchor`（棘輪單一來源）＋ summary 的
  11.703 → **11.9830** ＋ certified 新增 **C7／C8**（重建 HTML：`docs/mindmap/prefill250decode20.html`）
- 帳（棘輪制）：`Backup/caliber_unified_2026-10-01/audit_ratchet.json`
- `scripts/check/quote_gate.py`（iter_rows 硬化 ＋ selftest 案例；判準常數一行未動）
- `scripts/check/charters/e-caliber-unified-2026-10-01.yaml`（立項卡）
- 本檔（預註冊；§6／§6.1 回填）
- 帳：`Backup/caliber_unified_2026-10-01/audit.json`
- D17 棘輪一致性（`decode_board_build.py` 的 `ratchet_consistency()`）；看板十格補 `ratchet`／`ratchet_exempt`
- `caliber_certify.py --board-row`（入表片段帶 D17 欄位）與 `--update-board`（推上限＋補列＋重建產物＋`--check`＋紅就回滾）

---

## 8. 後記（2026-10-01 13:0x）：第一批 12+ 出現了 —— 入口照 §2 自己判出來

L20-7 的價格場次（權威 row、四條）同日中午跑完，把四條產品送**同一支入口**：

```
python3 scripts/check/caliber_certify.py certify Backup/rho_price_2026-09-30/order{1,2}_{A,B}.json \
    --kind decode --target-ts 12
VERDICT: 3/4 CERTIFIED
```

* `order1_A` **12.3027**／`order2_A` **12.3767**／`order2_B` **12.1265** ⇒ `CERTIFIED`（三條都**嚴格高於**
  棘輪上限 `11.982966`，且 ≥ 12 下限）；
* `order1_B` **11.6573** ⇒ `REFUSED`（兩條都不過：< 12 下限、≤ 棘輪上限）。

⇒ §6.1 的「可引用 decode 天花板 11.9830 ⇒ 0 個 ≥12」是**當下**的事實、不是永久性質；入口的意義
正在這裡 —— 它照 §2 自己判，不必逐件裁定。**operator 2026-10-01 決定：入表＋推上限** ⇒ 看板已新增 **C9 12.3767**（order2_A，新上限）／
**C10 12.3027**（order1_A）／**C11 12.1265**（order2_B），`certify_anchor.decode_ts` 推到
**12.376702**（cell `(default)`、artifact `order2_A.json`）；審計已重跑補帳：**2780 檔 ——
CERTIFIED 5**（三筆 prefill ≥250 ＋ order1_A／order2_A 的 prefill 288.4／281.1）、
**QUOTABLE_NOT_ABOVE_ANCHOR 8**（C8 11.98／C7 11.95／A 臂 11.63／10.92／10.75／9.377 ＋
兩條 B 臂的 prefill 206.1／206.2）、**REFUSED 894、NOT_JUDGEABLE 1827、UNREADABLE 45** ⇒
帳 `Backup/caliber_unified_2026-10-01/audit_ratchet.json`（舊上限那份留檔為
`archive_audit_ratchet_anchor11.9830.json`）。

⚠ 口徑不變：這三條是**權威 row**（`(default)` 格、逐 rep 3、逐臂 env 判身分），不是輪級聚合 ——
舊的 12+ 那一族（`Backup/s3b_abba_2026-09-30/*`）仍逐字判 `REFUSE`（R8c）。

判決的另一半：這四條同時是 L20-7 的**價格場次**，判詞是 `NO_EFFECT`（兩趟都不可解 ⇒ 量不出價格、
**不是免費**）。見 `docs/RHO_PRICE_AUTHROW_2026-09-30.md` 的 `## 判決：NO_EFFECT` 節；
看板 L20-7 已回填（`duplicate／verdict_exists／settled`＋兩個 `counter_quotes`）。

---

## 9. 後記（2026-10-01 14:0x）：棘輪與認證表的一致性（D17）＋ `--update-board`

operator：「讓看板 `--check` 驗『棘輪與已認證表一致』：`certify_anchor` 必須等於表上最高 decode，
且每一列入表當時都高於當時的上限，不符就紅。」

* **D17**（唯一實作＝`decode_board_build.py` 的 `ratchet_consistency()`；build 當場重跑）五條：
  ① 有 decode 讀數的認證列必須宣告 `ratchet:` 或 `ratchet_exempt: <why>`（都沒有 ⇒ 紅）；
  ② `ratchet.decode_ts` 逐字等於自己 `quote.artifact` 的 decode 讀數（容差＝`caliber_certify.HOLDER_EPS`，同一常數）；
  ③ `decode_ts > entered_above`（嚴格；`RATCHET_EPS` 只擋浮點噪聲）；
  ④ 入表時的上限只能是表上已有的讀數（或 0）——自創錨／跳號就紅；
  ⑤ `certify_anchor.decode_ts`＝表上最大 decode，且 artifact＝那一列的 `quote.artifact`。
  看板十格全部補上欄位：C3 `entered_above: 0`（棘輪未立）、C4 基準類 ⇒ `ratchet_exempt`＋why、
  C7 11.948299／C8 11.982966／C9 12.376702／C10 12.302695／C11 12.126469（C10／C11 與 C9 同批、上限都指 11.982966）。
* **入表片段帶欄位**：`caliber_certify certify … --board-row` 直接印含 `ratchet:` 的可貼片段
  （`decode_ts` 逐字抄 avg_ts；`entered_above`＝判這件時的看板上限）。紀錄本人印**哨兵字串**
  （前一上限由人填；貼上去 D17 當場擋，不靜默放行）。入表依據是 prefill 的列 ⇒ 改印 `ratchet_exempt`＋why
  （decode 讀數沒有高過上限時不硬寫 `ratchet`）；`value`／`why` 也改抄**入表那一軸**的讀數（不是全軸 `best`）。
* **推上限機器化**：`--update-board`（嚴格新高才推）：① 先 `--check`（現況紅 ⇒ 不碰）；
  ② 推 `certify_anchor`（表上還沒這一列就順手補進 `certified`，id 接下一個 `C` 序號）；
  ③ **重建產物＋`--check`**（改了 YAML 就一定 D4 落後——不重建不算驗完）；④ 沒過 ⇒ 整檔回滾＋產物重建回原樣。
  實測：`order2_A`（紀錄本人）⇒ 不推、看板逐字不動；`order1_A`（decode 12.3027 ≤ 上限）⇒
  best 是 prefill 也不推；合成新高 12.55 ⇒ 推升、補 C12（`ratchet: {decode_ts: 12.55, entered_above: 12.376702}`）、
  重建＋`--check` **PASS**（測試後逐字還原）。
* 閘門實測：`caliber_certify --selftest` **34/34**、`decode_board_build --selftest` **99/99**、`--check` **PASS（0 問題）**、
  `quote_gate` 31/31、`rho_price_authrow` 21/21、`runnable_gate` PASS、`landing_ledger` 16/16（結案 13）、`doc_claim_gate` rc=0。
  （看板字面：summary／certified_note 的「最高 …」`--update-board` 不代改，推完會提醒手動同步。）
