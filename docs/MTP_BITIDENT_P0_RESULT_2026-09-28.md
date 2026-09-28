# MTP ON bit-identical — P0 判讀（2026-09-28）

> 狀態：**P0 完成 ⇒ 依立項卡判讀表，進 P1**（P1 要 build，需約窗口）
> 依據 `docs/MTP_BITIDENT_CHARTER_2026-09-28.md` §4 P0 判讀表 · 本線（`線A (ace)`）
> 全部 0 build。判據**只看 `pos=209`（第一個 decode）的 logit**，不看 t/s。

---

## §1 結論：三趟（＋一趟鏡像）全部無變化 ⇒ 進 P1

| 趟 | 改動 | 生效證據 | `argmax_logit @ pos=209` | `row_fnv1a64 @ pos=209` |
|---|---|---|---|---|
| OFF 參考 | — | — | **19.227451** | `c0ae3d1f9f368985` |
| ON 基線（既有） | — | — | 19.258354 | `aa8f4329965b5d5e` |
| **P0-2** | `CGC_SERVER_DRAFT_NGL=0` | ARG 出現 `--spec-draft-ngl 0` | **19.258354** | `aa8f4329965b5d5e` |
| **P0-3** | `CGC_SERVER_MTP_N_MAX=1` | ARG 出現 `--spec-draft-n-max 1`；`n_tokens` 4→2、dump 176→87 行 | **19.258354** | `aa8f4329965b5d5e` |
| **P0-4** | pool 12 GiB | `-expert-cache 12884901888` | **起不來**：`CGC-METAL-FAIL status 5` OOM（load_model，compute #41） | — |
| **P0-4′** | pool 10 GiB | `-expert-cache 10737418240` | **空答案**（Metal OOM 簽名，gate 判 INVALID RUN，不採用） | — |
| **P0-4b**（鏡像） | pool **4 GiB** | `-expert-cache 4294967296` | **19.258354** | `aa8f4329965b5d5e` |

| **P0-5** | `CGC_SERVER_MTP_N_MAX=0`（不產生任何 draft） | ARG 有 `--spec-draft-n-max 0` | ⛔ **實驗不成立**：第一個 decode 後
  `GGML_ASSERT(n_outputs_max <= cparams.n_outputs_max) failed`（`llama-context.cpp:3057`）⇒ 配置非法，讀數不可當判據 | — |

⇒ **沒有任何一個鍵讓 logit 動一分一毫**，而且 `pos=209` 的 row hash 在**每一趟**（含 pool 砍半）
都與 ON 基線**逐位相同** ⇒ 這是單一、確定性的結構性分岔，不是 env、不是佈局、不是擠壓。
⛔ P0-5 **不成立**（`n_max=0` 是非法的：它在 `output_reserve` 觸發 assert），所以「零 draft」這刀
**沒能補上**；但 §2.2 第 2 條的零成本判據（`ctx_type='MTP'` 最小 step=2 ＞ 第一個 DEF decode step=1
⇒ **差異在任何 draft forward 之前**）仍然成立，等價的結論早就有了。

**旁證**：`pos=1`（prefill 尾）在所有趟（**含 OOM 前那趟 10 GiB**）都是 `27.578886`／
row `30f3eb924b6617dd` ⇒ **prefill／KV 完全精確**，分岔 100% 落在「第一個 decode 怎麼算」。

---

## §2 假設狀態（依本次實跑更新）

| 假設 | 狀態 | 依據 |
|---|---|---|
| **H5**（target ctx 因 draft 資源配置而不同） | ⛔ **證偽** | draft 全踢 CPU（`--spec-draft-ngl 0`，已確認進 ARG）logit 不變；draft 深度降到 1 也不變 |
| **H7**（`expert_index` 30720→31488 改變查表／分區） | ⛔ **證偽（雙向）** | pool 砍半（8→4 GiB，容量與 slab 佈局都變）logit 逐位不變；加大方向 12/10 GiB 在 16 GB 上 OOM 不可行 |
| **H6**（spec 路徑在首次 decode **之前**改了 `n_past`／graph 形狀 ⇒ 換了一條 decode 路徑） | ★ **仍活著，升為首要** | 唯一還能解釋「prefill 精確、第一個 decode 就分歧、且與 draft 深度／devices／pool 全無關」的假設 |
| **H8**（spec 本質上換了輸出函數 ⇒ bit-identical 不可達） | ★ **仍活著** | 與 H6 目前不可區分，需要 §4 的 P0-5 一刀切開 |

⇒ **方向從「改設定」變成「改引擎」**：P2-a（改 `run_server.sh`）已確定**做不到**，只剩 P2-b。

---

## §3 兩個方法論要點（避免下次白跑）

1. **每趟都驗了旋鈕真的進 ARG**，不是只看「有沒有傳 env」。這輪 P0-2／P0-3／P0-4b 都拿到了
   逐字證據（`--spec-draft-ngl 0`／`--spec-draft-n-max 1`／`-expert-cache 4294967296`）
   ⇒ 「無變化」是**真的無變化**，不是鍵被 allowlist 丟掉的假陰性（本線 09-28 已踩過一次 `!` 前綴坑）。
2. **pool 加大在 16 GB 盒上不可行**（12 GiB 直接 OOM、10 GiB 空答案）。之後不要再排「加大消除擠壓」
   這類實驗；要動佈局只能往**小**的方向，或用 cache-off（同樣高風險）。

---

## §4 建議先跑 P0-5（**1 趟，0 build，約 3 分鐘**）再進 P1

**為什麼值得**：P1 要 build、要約窗口、量測更貴。P0-5 是**同一個問題的 0 成本切分**，能決定
「P1 到底在找什麼」，甚至可能直接判定退出條件 H8：

```sh
# P0-5：MTP 路徑全開，但 draft 深度 = 0（不產生任何 draft token）
--env CGC_SERVER_MTP=1 --env CGC_SERVER_TEMP=0 --env CGC_SERVER_MTP_N_MAX=0
```

| 結果 | 判讀 | 代價 |
|---|---|---|
| logit **回到 19.227451** | 成因是「spec 路徑存在時 target 的 ctx／graph 建立方式」⇒ **可修**，P1 直接往那裡挖 | P1 照排 |
| logit **仍是 19.258354** | **一次 draft 都沒產生卻仍分歧** ⇒ 成因是 `blk.40` 載入／MTP block 進池本身 ⇒ 修法是「延後／隔離 MTP block」，**不是** decode 路徑 | P1 改挖載入期 |
| server 拒絕 `n-max 0` 或退化成 OFF | 實驗不成立，直接進 P1 | 無損失 |

⛔ 風險：`run_server.sh` 對 `SPEC_DRAFT_N_MAX` **沒有任何校驗**（直通 `--spec-draft-n-max`），
llama.cpp 端是否接受 0 未驗；不接受就標「不成立」，不做替代解讀。

---

## §5 P1 計畫（**要 build，需批准窗口**）

目標：在 target 的**第一次 decode**（`pmax=209`）上逐層 dump，找第一個分歧層 `il*`。

- **候選 (a)（建議）**：新增一個 env-gated 的 per-layer hash dump（只開在第一個 decode、
  只 hash 每層輸出張量的前 N 個 float）⇒ 成本低、不釘張量、不改排程。屬 `src/` 小改。
- **候選 (b)**：`CGC_TENSOR_CAPTURE` 只釘少量節點名。⚠ 既有結論標它為**擾動型**
  （釘張量會改排程器配置；全 graph × 41 層在載入期就 OOM abort）⇒ 只能當診斷，且**兩側要釘同一組**
  才能做差分。優先 (a)。
- 產物：`Backup/m123_oracle_gate/per_layer_first_decode_{off,on}.jsonl`。
- 判讀：`il* == 0` ⇒ 走向 H8（依立項卡 §6 失敗定義：**關閉本立項，改開「口徑重定義」**）；
  `il*` 在中間 ⇒ H6 成立，可修。

---

## §6 本次產物

- dump：`Backup/m123_oracle_gate/dump_p02_ngl0.jsonl`、`dump_p03_nmax1.jsonl`、
  `dump_p04_pool12.jsonl`（空）、`dump_p04_pool10.jsonl`（INVALID）、`dump_p04b_pool4.jsonl`（＋各自 `.cap`）
- 判據提取器：**`Backup/m123_oracle_gate/p0_pos209.py`**（以 `pmax + token_idx` 對齊，
  已在 OFF/ON/5 個既有 dump 上自驗：OFF 19.227451、ON 19.258354）
- 未動：`src/`、`scripts/run_server.sh` 一行未改；未 build；未跑任何 t/s 量測。
