# MTP ON bit-identical 立項 · 收尾判定（2026-09-28）

> 狀態：**建議關閉（本線判定）· 待 operator 追認**
> 前置：`docs/MTP_BITIDENT_CHARTER_2026-09-28.md`（立項）、
> `docs/MTP_BITIDENT_P0_RESULT_2026-09-28.md`（P0）、
> **`docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md`（P1 根因）**
> 本輪**沒有 build、沒有跑量測**，`src/` 與 `scripts/` 一行未改（只做源碼閱讀＋既有 dump 比對）

---

## 0. 一句話答案

**立項目標（A1–A5 全過）沒有達成，而且按立項卡自己寫的「失敗定義」已經觸發 ⇒ 建議關閉。**

但**不是白跑**：本輪把 H8 從「經驗上做不到」升級成「**源碼級證明**」，並補上一個之前缺的關鍵
事實（ON 的輸出不是品質缺陷）⇒ 「關閉」是**有證據的決定**，不是放棄。

| | |
|---|---|
| A1–A3（bit-identical 本身） | ❌ 未達成，**且已證明 config 路徑不可達** |
| A4（ON 自身可重現） | ✅ 達成（176/176，兩次獨立 launch 逐字相同） |
| A5（性能配對） | ❌ 未做（依附於 A1–A3，前提出問題就沒有意義） |

---

## 1. A1–A5 逐條結算

| # | 判據 | 結算 | 依據 |
|---|---|---|---|
| A1 | M1/M2 全 PASS（`only_A=only_B=0`） | ❌ | ON `n_compared=14`、M1 **1/14**、M2 **2/14**；OFF 對 OFF 參考 50/50 PASS ⇒ gate 本身是好的，是兩臂真的不同 |
| A2 | `pos=1`/`pos=209`/`pos=213` logit 逐位相同 | ❌ | `pos=1` 全臂 **27.578886**（prefill 精確）；`pos=209` OFF **19.227451** vs ON **19.258354** |
| A3 | greedy `probe_answer` 逐字相同 | ❌ | 見 §4，兩側答案不同（但**都通順**） |
| A4 | ON 自身可重現 | ✅ | `greedyon` 與 `greedyon2` 兩次 launch 答案逐字相同、M1/M2/M3 同為 1/14 / 2/14 / 1/14 |
| A5 | 乾淨窗口配對重跑 + A1–A5 | ❌ | 未做；`attribution='both'` 的配對數字不可引用，做了也不能結案 |

---

## 2. 為什麼判定關閉：**H8 的源碼級證明**（本輪新增）

P1 已定位：`delta-net-base.cpp:494` 的 `if (cparams.n_rs_seq == 0)` 是兩條 recurrent
（GDN／conv）狀態路徑的分岔。本輪把「`n_rs_seq` 從哪裡來」追到底：

### 2.1 `n_rs_seq` 完全由 spec 類型決定，沒有第三條路

```cpp
// src/llama.cpp/common/common.h:395
uint32_t need_n_rs_seq() const {
    bool needs_rs_seq = std::any_of(types.begin(), types.end(), [&](auto t) {
        return t == COMMON_SPECULATIVE_TYPE_DRAFT_MTP || ... EAGLE3 || DFLASH || DSPARK;
    });
    return needs_rs_seq ? draft.n_max : 0u;      // ← 只有 n_max 這一個變數
}
// src/llama.cpp/common/common.cpp:1645
cparams.n_rs_seq = params.speculative.need_n_rs_seq();
```

⇒ **開 MTP ⇒ `n_rs_seq = n_max`**。要讓它為 0，唯一辦法是 `n_max = 0`，而那是**非法配置**：
P0-5 實跑在 `llama-context.cpp:3057` 觸發
`GGML_ASSERT(n_outputs_max <= cparams.n_outputs_max) failed`（見
`MTP_BITIDENT_P0_RESULT_2026-09-28.md`）。

⇒ **`n_max ≥ 1 ⟹ n_rs_seq ≥ 1 ⟹ 走 K 槽位回滾分支`。不存在任何 config／env 旋鈕能同時
「MTP on」且「走原地路徑」。**

### 2.2 這直接解釋了 P0 的每一趟「無變化」

| 實驗 | 為什麼 logit 不動 |
|---|---|
| `MTP_N_MAX=1`（P0-3） | `n_rs_seq` 4→1，**仍 > 0**，仍走 else 分支（只看得見 `n_tokens` 4→2，看不見路徑） |
| `DRAFT_NGL=0`（P0-2） | 只改 draft 的資源配置，碰不到 target 的 `n_rs_seq` |
| pool 8→4 GiB（P0-4b） | 只改專家池布局，碰不到 recurrent 狀態 |
| 8 鍵 ablation（前一輪） | 沒有一個鍵碰到 `n_rs_seq` |

### 2.3 為什麼「走了 else 分支」就一定有數值差（不只是寫回位置不同）

把兩個分支的索引攤開（`delta-net-base.cpp:494-537`）：

- 原地分支：`s_idx = conv_input->ne[0] - conv_states->ne[0]`、`s_slot = 0`，**1 次** `ggml_cpy`。
- 回滾分支：`K = n_rs_seq + 1`，對 `t = 1..K` 各寫一次；`t = K` 時
  `s_slot = 0` 且 `s_idx = conv_input->ne[0] - conv_states->ne[0]` —— **與原地分支逐字相同**。

⇒ **槽位 0 被寫入的「位置與來源索引」是同一個**，所以差異不可能來自寫回邏輯本身；
它只能來自 **`conv_input` 這張圖本身不同**：實測每層 `conv_input` 節點 **30 → 150（=5×，即 K 倍）**、
`cache_r_l` **150 → 390（+240）**、`gdn` **30 → 60**，`conv_state_update` **60 → 0**
（else 分支沒有 `cb()`，故不產出該節點名）。

⇒ **conv／GDN 是在「K 個槽位一起算」的寬度下做的**，而 OFF 是「1 個槽位」。歸約寬度不同
⇒ 浮點累加順序／kernel 向量寬度不同 ⇒ 差 0.0309（0.16%）⇒ greedy 下足以換詞。

### 2.4 判定

> **H8 成立**：差異是「開 spec（MTP/EAGLE3/DFLASH/DSPARK）就換一條 recurrent decode 路徑」
> 的必然後果。`n_rs_seq > 0` 不是可以調的參數，是 `need_n_rs_seq()` 對 MTP 的硬編碼返回值。
> ⇒ **P2-a（只改設定／`run_server.sh`）原理上不存在。**

旁證（repo 內既有記載，`server-context.cpp:1405-1410`）：fork 自己就寫過
「MTP-on regime … M1 **1/9**，而同一個 build 對自己的 dump 9/9」，並因此拒絕了那個改動。
⇒ **這是已知的、被記錄過的現象，不是本輪新引入的 regression。**

---

## 3. P2-b 為什麼不做（成本 > 收益）

P2-b ＝ 讓「K 槽位一起算」對槽位 0 產生與「單槽位」逐位相同的結果。做法只能是讓 conv／GDN
kernel 對槽位 0 固定用單槽位寬度（把批次拆開）。

| 面向 | 評估 |
|---|---|
| 觸及範圍 | recurrent（GDN／DeltaNet conv）kernel，屬 compute 核心，不是本線的 `scripts/` 層 |
| 風險 | 拆批次幾乎必然**變慢**；而這正是 spec 最需要快的那段 |
| 收益 | 只換來「ON 與 OFF 可比」，**不換來任何速度**（見下） |
| 現有速度事實 | 同 build OFF tg 中位 **11.92** vs ON **7.34**；另一組 OFF 11.53 vs ON(cap16) 8.26（窗口不乾淨，**只可看方向不可定價**） |
| 存活成本 | ON 要活下來還得動 `CGC_SERVER_LAYER_CAPS=40-40:16`（拿 draft 命中率換記憶體） |

⇒ **即使 bit-identical 修成，ON 仍比 OFF 慢且更難存活**，而立項的初衷是「讓 ON 的數字可以用」。
⇒ **修好也是負收益 ⇒ 不做。**

⛔ 另一條捷徑被明確排除：強制 `n_rs_seq = 0` 換 bit-identical。按 `server-context.cpp:3306`
（`use_ckpt_tgt = FULL \|\| (RS && draft.size() > n_rs_seq)`）它會退化成每步 checkpoint restore
（正確但更慢），而且 `n_max = 0` 在本 build 直接 assert ⇒ **既拿不到、也不划算**。
況且這屬「用別的东西換 bit-identical」，與 §5 的護欄衝突。

---

## 4. 本輪新事實：ON 的輸出**不是品質缺陷**（決定「關」還是「必須修」）

這是關閉前必須補的一刀：如果 ON 的答案退化／胡言亂語，那它是 **bug**，必須修、不能靠
「口徑重定義」帶過。實測（`summary_greedy{off,on}-20260928.json` 的 `probe_answer`）：

| 臂 | 答案 |
|---|---|
| **OFF** | `台北101：地標建築，擁有世界最高可運作觀景台，夜景璀璨。`<br>`阿里山國家風景區：以神木、雲海、日出及小火車聞名，自然景觀壯麗。` |
| **ON** | `台北101：地標建築，兼具現代科技與傳統文化意象。`<br>`阿里山國家風景區：以神木、雲海及日出聞名，自然景觀壯麗。`<br>`墾丁國家公園：擁有…` |

判讀：
- 兩側**都通順、都切題、語義等價**，只是用字與截斷點不同 ⇒ **ON 不是 garbage、不是品質退化**。
- 且 ON 自身 **176/176 可重現**（兩次獨立 launch 逐字相同）⇒ 它是**一個確定的、自洽的輸出函數**，
  不是隨機損壞。

⇒ **「另一個輸出函數」這個定位是安全的** ⇒ 「口徑重定義」成立，且**不是拿品質換速度**。

---

## 5. 建議開立的替代立項：「MTP 口徑重定義」（草案，待批准）

**目標**：不再把 `CGC_SERVER_MTP=1` 當「純加速開關」，而是明文定位為
**另一個輸出函數**（與 OFF 不在同一座標系），並把這個事實**寫進工具與文件**，避免任何人
再拿 ON 的 t/s 去和 OFF 的基線比。

**判據（B1–B4）**：

| # | 判據 |
|---|---|
| B1 | `docs/` 與量測入口（`harness.py bench`）明確標注：MTP on 的 t/s **不可與 OFF 互比**，跨臂比較一律拒絕 |
| B2 | 引用 ON 數字時必須附帶「輸出函數 = MTP-on」標籤；既有把 ON 當 OFF 加速版的結論全部重標 |
| B3 | 交付口徑**維持 MTP off**（`prod-new` 的 `CGC_SERVER_MTP=0` 不動） |
| B4 | 若要重新追求「ON 當加速用」，**先決條件**是 ON 在乾淨窗口上快過 OFF（目前方向相反）⇒ 在此之前不投任何資源 |

**明確不做**：不放寬 M1/M2/M3、不換 cell／prompt 讓它「看起來一樣」、不改交付口徑。

---

## 6. 資產清單（後人可直接用，不要重做）

| 資產 | 內容 |
|---|---|
| `docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md` | 根因：`n_rs_seq` 0→>0 與 graph 結構差（3847 vs 4117 節點） |
| `docs/MTP_BITIDENT_P0_RESULT_2026-09-28.md` | 四趟 0-build ablation；pool 加大在 16 GB **OOM 不可行** |
| `docs/MTP_ON_OUTPUT_ABLATION_2026-09-28.md` | 8 鍵逐鍵 ablation 全排除；`pos=1/209/213` 對齊法 |
| `Backup/m123_oracle_gate/p0_pos209.py` | 以 `pmax + token_idx` 對齊的 logit 判據提取器（已自驗） |
| `Backup/m123_oracle_gate/p1_graph_names.py` | `CGC_GRAPH_NAMES` 的 pass／節點解析器 |
| `Backup/m123_oracle_gate/dump_p1names_{off,on}.jsonl` | 同 build 的 OFF／ON 對照 dump |
| 新儀器 | `CGC_GRAPH_NAMES=<n>`（`llama-context.cpp`，**默認 0 關閉**、不讀張量、不釘 buffer） |

---

## 7. 待 operator 裁定

1. **是否追認關閉**本立項（本線建議＝是，依據 §2 的源碼級證明 + §3 的負收益）。
2. **是否開立** §5 的「MTP 口徑重定義」立項（0 build，純文件／工具標注）。
3. 若 operator 仍要求追求 bit-identical ⇒ 那屬 **P2-b（動 recurrent kernel）**，需另約窗口、
   另立預算，且應先接受 §3 的「修好也負收益」評估。

⚠ 在完成裁定前，**交付口徑維持 `prod-new` 的 MTP off**，本線不改任何預設值。
