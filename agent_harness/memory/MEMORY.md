# flashkv-devserver — 專案長期筆記（索引入口，本檔要維持小）

> **這是快照，不是權威副本。**
> 權威位置：`.workbuddy/memory/MEMORY.md`（由 host 持續寫入）。
> 本檔於 2026-09-27 由 `agent_harness/scripts/import_harness_snapshot.py` 複製進 repo，唯一目的是讓 `agent_harness/`
> 底下的內容能被 `agent_harness/scripts/auto_git_push.ps1` 定時推送；原檔改了這裡**不會**自動跟上。
> 索引與漂移檢查見 `agent_harness/engine_loop/memory/INDEX.jsonl`。

llama.cpp 的 CGC fork：Metal ＋ **expert cache pool**（專家權重常駐 SSD→池）。根
`/Users/alexchuang/Documents/flashkv-devserver`（**git worktree**，`.git` 是檔案）。
逐日經過在 `.workbuddy/memory/YYYY-MM-DD.md`（append-only，本線用 `§EN-` 前綴）。

## 讀法：1 索引（本檔）＋ 4 主題檔

| 檔 | 什麼時候讀 |
|---|---|
| **`MEMORY.md`**（本檔） | 每次動手前（只給導航與一句話） |
| `MEMORY_PERF.md` | **要引用任何 t/s／profile／幾何／散熱數字之前**；末節＝ K0–K5 結案總表＋歷次瘦身移入（**第八輪＝本檔 09-26 瘦身的全文**） |
| `MEMORY_S1.md` | 要碰 S1／分歧定位／`CGC_TENSOR_CAPTURE`／池查表之前 |
| `MEMORY_FACTS.md` | 動 build／載入／預算／`-ub`／mmap、要碰 `agent_harness/`、或**要提交**之前 |
| `MEMORY_HYGIENE.md` | 動量測／起 server／要引用環境坑、儀器口徑坑或入口指令之前 |

- 舊報告寫的「`MEMORY.md` 的 S1 節」＝ `MEMORY_S1.md`（dated 產物，不回改）。
- **新增／刪除這底下任何 `.md` 都要重生索引**（`build_memory_index.py:66`）。
- ⚠ `index_assets.py` 範圍＝`scripts/check/*`＋本目錄＋`docs/*` 的**顯式註冊表** ⇒ 新增 `docs/`
  檔案不會自動進 MANIFEST，也不會報漂移。
- ⚠ **截斷判準**：「~10KB」單位是**字元**。只有本檔被截＝該瘦身（09-21/22/23/24/26 各瘦一次，
  內容一律移入 `MEMORY_PERF.md` 末節）；本檔與雲端 `<memory>` 一起被截＝總量預算，別動本檔。

## 現在的一句話狀態（**主題檔為權威**）

- ★★ **主線目標（operator 09-26 09:5x 權威表述）＝ 目標函數 ＋ 硬約束**：
  **把除 compute time 以外的時間（gap／wait／cb／fill）最大程度消除，同時 M1/M2/M3 保持
  bit-identical。** M1/M2/M3 是**護欄不是獎勵**（不能拿它換速度）。任何槓桿先問兩題：
  (a) 它砍的是不是這四個桶之一（砍 compute 的不算業績）；(b) 砍完 M1/M2/M3 還 identical 嗎。
- ★ **靶的帳**（`STEP_SERIALIZATION_2026-09-23.md` §1 ＋ `GAP_ELIMINATION_PLAN_2026-09-24.md`；
  verify step、`segs=41`、n=221）：total(CPU) **161.0** ＝ wait 123.2(78.1%，CPU 自旋等 GPU，
  **不是浪費**)＋ cb 23.8 ＋ submit 10.8；GPU 側 **union 113.4（真忙）＋ gap 44.4（空窗）**。
  ⇒ 唯一能動的桶 = **gap 44.4**；能消它的機制只有一個：段數 41→1。⛔「把 IO 藏進空檔」必敗
  （**空檔本身就是 CPU 造成的**，r=0.957／slope=1.05）；fill 同步僅 3.955 ms＝4.7%。
- ★★ **09-26 `CGC_CB_N_MAIN`（Metal 編碼切分；預設地板 64）＝ 目前最高價值候選**：ABBA
  `32` vs `64` ⇒ `total −11.4%`／**`tg +12.8%`**（處理臂散佈 0.17%）；**D5 長探針 PASS
  `1045/1045`（含 447 MTP 行、`only_A=0`）⇒ 同樣 logits、更少時間 ＝ 數值免費**。
  桶（`CGC-GPUTIME`）：`64` ＝ **union 73.1 ＋ gap 15.7**（gap ≈ 步長 19%）；⛔ **`union` 不是不變量**
  （`nm16` 用同一組節點把它打到 64.5）⇒ **拆分必須逐臂量**。★ **掃描更正**：`16` 是陷阱（比 64 差 26%）、
  `128` 中性（但 `cb` −38%）、`32` 值得追、**預設 64 中庸不是最差**。⛔ **`32` 的 union/gap 仍未量**。
  ⇒ `MEMORY_PERF.md` 第九輪（＋補二）＋ `docs/CB_N_MAIN_BUCKETS_2026-09-26.md`。
- ✅ **D5 紅燈已定案（P0，09-26 10:2x）＝「基準的語意基準移動了」**，不是儀器故障也不是 bug；
  護欄 M1/M2/M3 本身有效。判讀一個 D5 FAIL 的**三問順序**、以及閘門該用 **(c) 同 build 對照臂**
  （不是 (a) 重校準 pin、也不是 (b) DEF 子集 —— (b) 已作廢：draft 也走同一套池／hook）
  ⇒ **全文 `MEMORY_PERF.md` 末節第八輪 §A ＋ `2026-09-26.md` §EN-10:1x/10:2x ＋ skill
  `cgc-commit-gate` §2.4c**。
- ★★ **09-26 結案：本線表上一格不剩。** 判「**gap ≡ 44.4 ms 是 fill 的窗口，不可分離**」＝終局；
  三條獨立機制級證據（① 每層 hook 的 `drain_layer` 清掉未開始的 prefetch；② 預測性預填實測判死：
  `h = 0.022~0.032`、門檻 0.65、top-16 結構上界只 18~27%；③ 三個預測機制**共用同一來源**
  （`:5890`「Same prediction source」），而其「相鄰 token 重合 70~90%」與交付形狀（ntok=4）實測的
  51~56% 直接衝突）⇒ `docs/S1_CORRECTNESS_SPEC_2026-09-26.md` §8 ＋ `2026-09-26.md` §EN-10:4x。
  ⚠ 唯一還活著的是「保留分段、把 hook 做便宜」—— **常數優化，barrier 12.9/158 ≈ 8%**。
  ⚠ **教訓：「排在待辦最前」≠「還沒做」** —— 引用任何牌前先看它有無量測欄與判定。
- ★ **期望值最高的軸 ⇒ `E`（accept／mean len）**：**全專案第一次可量**（`MTP_CTX_REPRODUCIBLE`
  證明它是配置的函數、非抽樣；五次 launch 同一字串）。上界 **+19%**，**不碰 kernel／leaf／預測**。
  現值 **1.70**（切換前那支報 2.02 ⇒ 不同軌跡不可直接比）。⇒ `docs/OPPORTUNITY_MAP_2026-09-26.md` §3。
- ★ **交付 decode（唯一對外門，`harness.py bench --arm "prod-new"`）＝ `10.84 t/s`**
  （09-27 09:45，`contract.ok`＋`base_check.pass`，`engine_build=825ec07d5`、build 627、
  MTP off）。`pp 230.35`。單臂噪音底 ≈ ±27%，**3% 門檻**。⚠ `10.84` 附帶
  `attribution.verdict="swap"`（`swap growth +2406`、max 8382）⇒ **是上界不是中位**。
  ⛔ 28.15／池 wired 歸屬／wired_probe SIGABRT ⇒ `MEMORY_PERF.md` 第七輪 §A。
  ⚠ **新的對角**：本線常被引用的「交付口徑 11.03~12.20」是 **09-24 兩趟污染讀數的並集**
  （`12.20` 被本線判「不可引用」、`11.03` 是 `swap=6043` 承壓），且被錯掛在「契約 §7」名下
  （§7 是「使用入口」，判據是 `decode >= 10`）。⇒ **本線無乾淨的 MTP off 歷史基線可比**
  ⇒「t/s 有無進步」**方法論上不可判定**。⚠ **`12.57` 不是 OFF 參考**：含 MTP ON＋老 cell
  （`-b 512 / -p 0 / --ctx-size 4096`）。
- ★ **16GB 上唯一實測存活的組合＝ `--load-mode none` ＋ ngl 99 ＋ pool 8 GiB**（mmap 0/2 載入即死、
  none 2/2 存活）⇒ 可說「跑得動」，**不可說「最快」**。**prefill 250+ ＝ 已達標**（峰值 296.24、
  乾淨視窗 283.01），未達的是**可重現的地板**；白皮書的 `162.5 → 209.5（+29%）` **作廢**。
  ⚠ none 臂 6 個 prefill 讀數 228.94–258.25（3/6 ≥250）⇒「每次都 ≥250」不成立。
- **天花板** L0 12.57／L1 1.33%／L2 0／**L3 = 0**／L4 ~75（不可及）→ `CEILING_STACK_2026-09-21.md`。
  ⚠ **09-27 註**：`L0 12.57` 的錨本身不可作 OFF 參考（含 MTP ON＋老 cell）；天花板應以
  **對外門 prod-new `10.84`** 當下界起算。
  ⚠ **原值（L1 +5.6%／L2 +30~38%／L3 18.59）與 09-25 複核值（1.33%／0／0）不一致**，複核為準；
  三者**判 0 的依據性質不同**（L1 量測／**L2 儀器判定**／L3 機制＋量測）⇒ `OPPORTUNITY_MAP_2026-09-26.md` §5。
- **★ 里程碑**：`MILESTONE_MAP_RECHECK_2026-09-25.md` **取代** 09-21 原表判決。本線：M-L1 不做／
  M-W ≈+1.7%／M-L3、M-PF 判 0／M-S2 不可達／**M-K5 不做**／**M-25 判死**（fill 同步僅 4.7%）。
  ⚠ M-F5／M-CB 是**線 I** 的格子，本線無實測權，「表一格不剩」只在**本線範圍內**成立。
- **K0–K5 全部結案**；**MTP／spec**、**G4／S2** 已結案 ⇒ `MEMORY_PERF.md`。
  ⛔ 交付 cell 的 MTP 增益 **UNRESOLVED**、C1 矛盾（ASL 3.117 vs E 1.62）未裁定。
- ★ **ρ 落地面（09-26）**：`+14.2%` 是**模型上界**、`+4.7%` 是 **MAXQ 內部調參差** ⇒
  **ρ 在交付 cell 的淨增益從來沒量過**；且 capture 的同步回讀是「**第五個桶**」（96.9% 浪費）。
  **已修一個真實缺陷**：`cgc_rho_prefetch` 缺 prefill 閘（實測 `per_layer=255`，≈ 整層）⇒
  改用 `cgc_is_decode_graph` ＋ 加容量防線 ⇒ `2026-09-26.md` §EN-11:3x。
  全文 `Backup/RHO_LANDING_PLAN_2026-09-26.md`。

## ★ S1（單段提交）—— 已結案，細節在 `MEMORY_S1.md`

★★ **權威單頁 `docs/S1_LINE_VERDICT_2026-09-25.md`（引用前必讀）**：同 build 同 cell 乾淨分解
A 分段 **11.30** → B 單段 **20.73**（**×1.83**，兩臂皆無 spec）→ spec k2 22.45（+8.3%）
⇒ **那個 2× 是 S1 的，不是 MTP 的**。⛔ **>20 t/s 的 S1 讀數判不可信**（garbage／無 `answer_md5`）；
**「23.3 t/s」不可引用**。★ **撞名**：探針臂（`CGC_SLOT_TABLE_GPU`，576/576 通過但**無速度主張**）
vs 單段提交（`CGC_SEG_BATCH+CGC_B_SCHEME`，無 hook ⇒ garbage）—— 引用前先問是哪一支。

- **主項是序列化不是 fill**（消掉 40.3 ms 裡 fill 只 3.96 ms／4.7%）。
- ★ **G2（單段下的 miss 率）＝ 42.93%**（`SPEED_ACCEPTANCE_GATE_2026-09-26.md` §9.8）。⛔ 09-26 03:0x
  的「100%」**已作廢**：publisher 因 `cgc_node_in_graph` 只查 op 節點、不查 `leafs` ⇒ **一個位元組
  都沒寫**。兩個數字**都不可拿去定價交付 cell**（本臂＝「單段＋hook 不跑」，fill 不會發生）。
- ★ **G1b／G3 已實跑（09-26 14:0x）**：**G3 生效**（`zero_slot=142=ns-1`、`placeholder=0`）但
  **是 G1 的前置不是解**；**G1b＝0.395 ms/step（超其 0.2 ms 預算 1.97×），99% 在 78 次小讀、`sync` 只 3.8 µs**。
  ⚠ 全文 `MEMORY_S1.md` 末節（含 `g1b_g3_smoke2/` 路徑與 `e % ns` 已不可達的證據）。
- ⚠ **`cgc_node_in_graph` 對 leaf 一律 false** ⇒ `llama-context.cpp:3919` 的 `rm`（rn_mask）、
  `:3923` 的 `tb`（slot table）**長期靜默為 nullptr** ⇒ 靠它們推出來的結論要重跑。
- ⚠ **fill 是正確性前提不是速度前提**；開關兩半分在 `libggml-base`／`libllama` ⇒ **驗 binary 要總掃 `*.dylib`**。
- **全文（1.72×→1.83× 修訂、池計數器塌縮、k-sweep…）⇒ `MEMORY_S1.md` 末節第六輪移入。**

**其餘所有線（ρ／prebind／方案 A／k=3 飄移／cb 42vs74）是 09-23 的 dated 判決**，全文在
`MEMORY_PERF.md` 末節第四輪移入。一句話：ρ 與 prebind 各自判活、同 regime 同價
（+14.0%~+14.5%，12.57 → ~14.3~14.4），`cb` 定讞 42~51 ms（74.18 撤回）；**但 S1 出現後都降為第二順位**。

## 分工

**`agent_harness/` 歸另一條 session；引擎層（`src/`、`scripts/check/`、decode／prefill 量測）歸本線。**
動手前後各跑 `git status --porcelain -uall`；看到不是自己的 modified／staged 檔就停手、只 stage 自己的檔案。

- **本線 ＝ `線A (ace)`**（09-20 operator 裁定），定義按擁有物：S1／段邊界（`wait`／`gap`／`S2`）
  ＋ 逐層 KIND×OP 儀器。**另一條沿用 `線 I`**：`cb`（＝ expert cache 填池 IO，**不是** command buffer）
  ／快取命中儀器。命名表與五個碰撞面在 `docs/ENGINE_LINE_ASSIGNMENT_AND_G1_LADDERS_2026-09-20.md`。
- **09-23 起另有 `docs/NEXT_ACTIONS_2026-09-23.md` 的分工**：【WorkBuddy】＝靜態分析／長報告、
  【執行 Agent（freebuff）】＝跑 GPU 實驗／改量測工具。**照表做自己那一區，不要做別人的事。**
- ⚠️ **共用碰撞面（09-20 實測）**：① `llama-context.cpp`；② `ggml-backend.cpp`；
  ③ `libggml-base` 與 8080／GPU 窗口；④ **`cb` 的口徑**（兩個都可能對）；
  ⑤ `.workbuddy/memory/*.md` 併發寫入（gitignored ⇒ 無版控安全網）。
- **意圖不衝突但別相加**：F2 打 `cb`、S2 打 `wait`；分量相加在該 regime 不閉合。

## 量測衛生／入口／環境坑 → `MEMORY_HYGIENE.md`

★★ **量測契約九條／分級 17 列／作廢數字 292 處** 全文 ⇒ `MEMORY_HYGIENE.md` 末節（第六＋七輪移入）。入口 `docs/MEASUREMENT_CONTRACT_2026-09-25.md`；機檢 `formula_audit.py`／`void_number_check.py`；總圖 `docs/mindmap/`（44 條目）。⛔ commit `65c76b8c7` D5 未過（coverage 60%）⇒ owner 重新基線前勿引用。
