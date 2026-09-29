# server 窗口／box 准入（單一來源閘門） — 技術白皮書　·　3b ③b 實驗目標達成（可放生產）

> **一句話**：建立 server 窗口／box 准入的單一來源閘門，避免兩條線同時搶 GPU 而互相蓋掉對方的 build。

- 主題：系統／記憶體　·　子目標：**不適用**（非攻關軸：約束／帳本／上界算術／入口索引／跨線產品）
- 階段：生產（可放生產／已交付）　·　① ② ③b —— 已進生產、或已是生產決策依據

---

## 1. 目標

建立 server 窗口／box 准入的單一來源閘門，避免兩條線同時搶 GPU 而互相蓋掉對方的 build。

## 2. 判準

窗口是否乾淨（thermal ＋ 進程 ＋ swap）

## 3. 結果

BOX_ADMISSION_SINGLE_SOURCE 定為單一來源；SERVER_WINDOW_LEDGER 記錄逐次窗口。**09-29 21:29 實測：`admits=False`（need 8000 MB、reclaimable 7362 MB、缺口 ~638 MB）** ⇒ 交付 cell 的乾淨窗口**現在拿不到**（依 L25-3 的 on_fail：不降 NEED_MB、不換 cell）。 **09-29 21:33–21:36 實跑一對（prod-new 控制 ＋ S1）**：共用探針 `admits=False`（need 8000／reclaimable 7245 MB、binding=harness、與 launcher **DISAGREE**），但 bench 的起跑閘全過、`refused_preflight=False`；結果兩臂都換頁（swap growth **+1768／+851 MiB**）⇒ `attribution=swap`。⇒ 沒有乾淨窗口的代價已被量到，不是推測。 **§53（09-29 22:0x–22:36）窗口攻堅**：`reclaimable` 的定義是 `(free+purgeable+inactive)×16 KiB ≥ 8000 MB`。三個槓桿都量過：**idle 回收往下跌**（6596→6482／30 s）、**`purge` 反效果**（6482→4376 MB，因 file cache 正是被量的 `Pages inactive`，`free` 66→63 不動）、**關掉 app 有效**（→8006，稍後 **9736 MB**）。第 3、4 場**無 override 起跑** ⇒ 記憶體項**已過**；但 4 場全非 `attribution=none`（`swap growth` 338／1394／2401／4830 MiB）⇒ 剩下的 blocker 是**超額配置**（13.6 GB 模型＋8 GiB 池／16 GB 盒），不是時機。 **§54（09-29 22:40）第一場 `attribution=none` 達成**（8 GiB、交付 cell、無 override：`swap_growth=356.44 MiB`、`thermal=NOMINAL 126/126`、起跑 reclaimable 8083 MB）——**但該場 tg = 8.26 ± 3.79 t/s（121.1 ms）**，比錨點 11.703 慢 **29%** 且離散 **46%** ⇒ **`attribution=none` 是必要不充分**（乾淨標籤不保證同一個東西）。同時：**「縮池」在三層都不通**——env 送不到引擎（driver rc=1）、`expert_cache 8589934592` 是 cell 的**宣告機器不變量**（不符 ⇒ fail-closed）、且樹上已有「縮 pool 方向否決／4 GiB 會製造假的 treatment 效果」的裁決。 **§55（09-29）引用閘門**：窗口標籤（`attribution`）**必要但不充分** —— 交付 cell 唯一一場 `none`（22:40）的逐 rep 是 [10.45, 10.44, **3.88**]，第 3 個 rep 慢 **2.7×**。判準改由 `scripts/check/quote_gate.py` 持有（逐 rep 離散 `max/min ≤ 1.10` ＋ reps≥3 ＋ 窗口 ＋ 選配參考帶），且**看板 build（D7）會當場複判**：宣告的可引用性與閘門判定不符就失敗。⇒ 本節點要的窗口現在定義得更嚴：**`none` ∧ thermal NOMINAL ∧ 逐 rep 一致（全落在 1.10 內）**。

## 4. 判定

**3b · ③b 實驗目標達成（可放生產）** — 產物已可放進生產級設置：不破壞正確性 ∧ 成本可接受 ∧ 無前置條件

> 與本線的 gate 規格同源（契約 §3） **§53**：門檻 500 MiB 低於這支臂自己的成長分布（550–2984）⇒ 原文自陳「拒或不拒有一半是抽籤」。

---

## 5. 子目標綁定 · options · 測試 Log 報告

- **子目標**：`L20-10`　格子判別：量測起點 vs batch
- **來源**：子目標看板（唯一來源）　·　置信度 `high`
- **profile**：`prod-new`
- **arm 1（可複製）**：`prod-new`
- ⚠ **本格沒有旋鈕**：本格沒有既有旋鈕可動：batch／prompt／warm_skip 都是測試卡 §2.5 的嚴格維度（改了 fail-closed 拒跑），現有孿生機制只開放 reps／rep_split ⇒ 這是一個 cell 定義的決定，不是一次跑得動的實驗。
- **結案狀態**：未結案（§56 於 09-29 立項：跨格子不可比已確定；兩個候選尚未分離）
- （同條另掛：`L25-3`）
- **逐條處置**：整合進子目標　→ `L25-3`　—　box 准入單一來源閘門 —— 乾淨窗口那一格就是它
- **逐條處置**：整合進子目標　→ `L20-10`　—　量測起點（同一行程裡 pp 測試的有無）屬於窗口條件
- **測試 log**：無實跑 log（本條的證據是下面的「對應報告」，不是量測產物）
- 判別句互斥：B1（起點）⇒ 加大 warm-skip 的 delivery 升到 ~11.5；B3（batch）⇒ (default) 的 batch-512 孿生掉到 ~9.7。

## 6. 與其它條目的關係（同軸／同階段，自動對照）

| 條目 | 級 | 結果（摘） |
|---|---|---|
| [prefill ≥ 250（交付 cell）](m-prefill250.md) | 2 | **已認證（C1）**：9 次 launch ≥250，最高 **296.24**；乾淨視窗 **283.01**；同期 decode 11.49~12.20。（結案規則下，這是本 |
| [① 攻關成功（pp≥250 ∧ tg>12.57）](m-total.md) | 1 | ⛔ 空 —— 最接近的一次是同 cell pp **260.41** ＋ tg **12.195**（差 3%）。 |
| [swap 結構修復（L0–L4 + P0/P1/P2）](sys-swap.md) | 3b | launch swap 0、decode 11.49、thermal NOMINAL（commit efba7c1d5） |

## 7. 依據 · 備註 · 對應報告

| 項目 | 內容 |
|---|---|
| 依據 | `docs/BOX_ADMISSION_SINGLE_SOURCE_2026-09-20.md／SERVER_WINDOW_LEDGER_2026-09-19.md` |
| 備註 | 與本線的 gate 規格同源（契約 §3） **§53**：門檻 500 MiB 低於這支臂自己的成長分布（550–2984）⇒ 原文自陳「拒或不拒有一半是抽籤」。 |
| 軸性質 | 非攻關軸：約束／帳本／上界算術／入口索引／跨線產品 |
| 對應報告 | 2 份 |

- [BOX_ADMISSION_SINGLE_SOURCE_2026-09-20.md](../../BOX_ADMISSION_SINGLE_SOURCE_2026-09-20.md)
- [SERVER_WINDOW_LEDGER_2026-09-19.md](../../SERVER_WINDOW_LEDGER_2026-09-19.md)

---

← [swap 結構修復（L0–L4 + P0/P1/P2）](sys-swap.md)　·　[總目錄](index.md)　·　[HTML 版](sys-window.html)　·　[M3／M4／M5 離開條件（09-17 期） →](m3.md)

本檔由 `scripts/check/mindmap_brief_build.py` 從 `docs/mindmap/mindmap.json` 機械生成；改內容請改 JSON 後重跑，勿直接編輯本檔。
