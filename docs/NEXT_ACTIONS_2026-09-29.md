# NEXT_ACTIONS 2026-09-29：m-decode25 拆解的 `on_fail` 登記（缺槓桿 / 缺儀器）

日期：2026-09-29（日本）　分支：`demo/sweet-spot-windows-fix`　HEAD：`2823fdc76`
狀態：**這是 `scripts/check/charters/e-decode25-blocks-2026-09-29.yaml` 的 `acceptance.falsify` / `on_fail`
要求的登記。它不是新候選，是一份「已經量掉的東西不要再掃一遍」的清單。**

---

## 0. 為什麼有這份文件（觸發條件）

卡自己的 `falsify`：*任一塊拿不出實測槓桿 ⇒ 標為無槓桿並移除；移除後剩下的塊加總 > 40.0 ms ⇒ 本拆解不成立*。

以各塊**已量上界**代入：`19.5（kernel）＋ 12.0（gap）＋ 5.8（圖外）＋ 3.9（填充提交）＝ 41.2 ms ＝ 24.3 t/s`
> **40.0 ms** ⇒ **以 ms 為軸的這個拆解不成立**（§39 §二、§40 §一）。

`on_fail` 指定的兩件事：
1. **把無槓桿的塊登記進 NEXT_ACTIONS，並註明「缺的是槓桿還是儀器」** ← 本文件；
2. 判定 `SG-KERNEL` 是否也無槓桿（見 §2）——它決定 `m-decode25` 的判死是否維持、力氣是否轉 `m-total`。

依據：`docs/SINGLESUBMIT_CORRECT_DESIGN_2026-09-28.md` §39（盤點 + C1–C7）、§40（四個未死塊的推進方式）。

---

## 1. 登記表

| 塊 | 判詞 | **缺的是** | 依據 | 下一個能做的動作 | ⛔ 明確禁止（同一條路不要再走） |
|---|---|---|---|---|---|
| `sg-gap-boundary` | 🔴 **FAIL（附機制）** | — | §37（段數是資料依賴、`CB_N_MAIN=1` 淨 −1.93 ms）、§38（殘項是 Metal 側延遲） | 無 | 往邊界窗再塞 CPU 儀器；把「降 gap」當加速 |
| `sg-offgraph` | 🔴 **無獨立槓桿（附機制）** | — | §40 §二/§三：產品確實每 token 全同步（`server-context.cpp:4170` → `sampling.cpp:611`），但 §38 的 `gap_not_cpu` = 0.1545 ms/邊界 把「等待」上界壓到 0.31 ms ⇒ 8.858 ms 的 ≥96.5% 是尾端 GPU 忙碌 | 無（單流） | 再用「圖外減半 −5.8 ms」示範值；把它記成「缺業主」 |
| `sg-kernel-busy` | **無槓桿（未判死）** | **槓桿**（缺承載算子） | §18 §七：六族合計 21–29 ms 但**單點 ≤0.79 ms**（`uni` 只識別得出 `RMS_NORM`）；§23（逐 dispatch 合併已關） | **§18 §七(4) 的 skip-probe**（見 §2） | 在 skip-probe 之前寫融合 kernel；用 63 µs/dispatch 的外推當單價 |
| `sg-fillsub-spin` | **部分（未判死）** | **分離**（fill 成本 vs 正確性前提，C5） | §36：`utility topK resident 8/8 → 4/8` 而 `n_prefetch_dropped` 僅 39 ⇒ 丟失在**駐留期** | 駐留**生命週期**探針：對被需要的 id 記「上次填入 / 被逐出」時戳，把 miss 分成 *never-filled* / *evicted* | 再掃 `CGC_SPAC`／K／REFRESH（§36 已把旋鈕方向量成懸崖） |
| `sg-order-legal` | **未判死**（否證的是**其中一個修法**） | **機制**（新） | §28 E1（remap 上 GPU）實測**值 0 ms**；§24 §五（RACY 的機轉） | **唯讀**調查：remap leaf 的 producer→consumer，消費者能否直接吃 device buffer | 把 E2（`CGC_SUBMIT_AHEAD`）當成果；把 E1 的 0 ms 當「這條路已試完」 |

---

## 2. `on_fail` 第二支：唯一能關掉它的動作

`on_fail` 寫「若 SG-KERNEL 也無槓桿 ⇒ m-decode25 的判死維持，力氣轉 m-total（pp≥250 ∧ tg>12.57）」。

SG-KERNEL 的「有無槓桿」**還沒被量過**（§18 §七 給的是外推：六族的集體貢獻 21–29 ms、
單點 ≤0.79 ms）。§18 §七(4) 已經把動手前的那趟量測寫好：

> **skip-probe** —— env-gated、預設關、跳過所有**輸出元素數 ≤8** 的 dispatch
> （`SUM_ROWS`[1]／`CLAMP`[1]／`DIV`[8]／scalar sigmoid）；輸出**必然是錯的（VOID）**，
> 但把「群集 1 的 7.4 ms 上界」換成**實測 Δstep**。

* 這是本 repo 已有的模式（`CGC-HOOK=0`、「權重不進池」都是靠**故意錯的臂**量上界）。
* **它是四個塊裡唯一「一次 build ＝ 一個答案」的動作**，且答的正是 §2 這個分支問題。
* 在它跑出來之前：不寫那顆 300 行的 `ffn_moe_weighted`（G4 §4.3 已判它過不了 G4 門檻，
  §18 §七 補的是「連 3 ms 都還沒被證實」）。

**若 skip-probe 判 SG-KERNEL 無槓桿** ⇒ 執行 `on_fail`：
`m-decode25` 維持**判死**，力氣轉 **`m-total`**（pp≥250 ∧ tg>12.57 的同一場）。
現況：`m-total` = ⛔ 空，最接近的一次同 cell **pp 260.41 ＋ tg 12.195（差 3%）**
（`docs/mindmap/briefs/m-total.md`）。

---

## 3. 唯一沒有被任何一塊覆蓋的產品決策

| 項 | 現況 | 誰能決定 |
|---|---|---|
| **C6：MTP on/off** | 交付口徑 MTP **off**（契約 §7）；唯一「**乘**」而不是「減」的軸；on 的交付 cell 讀數 UNRESOLVED（12.62 已作廢），生產 ABBA 反而 **×0.695** | **產品決策**，不是技術量測 |
| **C7：乾淨窗口** | §33–§40 全部 `attribution=swap` ⇒ 連「這一輪有沒有推進」都答不出來 | 需要一次 `attribution=none` / NOMINAL / reps≥3 的窗口 |

---

## 4. 本文件不做什麼

* **不是新候選清單**：本文件不含任何「換一個旋鈕再試一次」的項目。
* **不改任何算術**：`§16 §四` 維持 §37 修正後的 **41.2 ms ＝ 24.3 t/s**（示範行裡「圖外減半 −5.8 ms」
  的**依據**已於 §40 §三 撤銷）。
* **不新增可引用的 t/s**：§40 是 **0 跑 / 0 build** 的轉引 ＋ 一條上界推論。
* 本文件與 §40 **未 commit**；`docs/mindmap/` 未動。
