# CGC pool／wired 預算：16 GB 盒子上的真實瓶頸（2026-09-28）

本線（線 A）受 operator 授權跨線處理線 I（expert cache／`cb`）的記憶體預算議題。
**本輪 0 build、0 程式碼改動**，全部結論來自既有 5 份實測 json ＋ 源碼／腳本註釋。

## TL;DR（三條，都與既有認知相反）

1. **「記憶體不歸還」是錯的。** 每趟結束 `pages_wired_mb` 都回落到 1778–7723 MB
   （`on_k3` 結束只剩 1778，幾乎全還）⇒ 歸還本來就在發生，不需要「解決歸還問題」。
2. **真瓶頸是「單趟峰值共存」，不是累積。** `wired 峰值 ≈ 12.4 GB`（模型權重的 Metal
   駐留）＋ `pool 8 GiB` ＝ **20.4 GiB vs 實體 16 GiB** ⇒ **必然有 ~4.4 GiB 的 pool 頁在 swap 上**
   ⇒ 這就是 `cb` 桶的物理來源（每次 miss 都要從 swap 換回）。
3. **pool 不能縮。** 同日真曲線（2026-09-25，`scripts/run_server.sh:566`）：
   **8 GiB 12.06 → 6 GiB 10.48 → 4 GiB 7.67 t/s**（8→4 慢 **36%**），因為
   **熱集（143/256 專家吃 98.8% 路由）需要 8 GiB 才裝得下** ⇒ 縮 pool 會掉出熱集。

⇒ **結論：這不是「加記憶體」或「縮 pool」二選一，兩條都已被數據關掉。**
真問題是「模型權重的 wired 足跡（12.4 GB）把 pool 擠到 swap 上」。

## 一、實測：wired 峰值恆定、且會歸還（5 趟，`Backup/phase_decomp/mtp_kbracket/`）

| 趟 | wired 起跑 | **wired 峰值** | wired 結束 | available 起跑 | min_free |
|---|---|---|---|---|---|
| off_a | 1922 | **12364** | 7123 | 6613 | 14.2 MB |
| on_k1 | 1688 | **12423** | 6051 | 7703 | 14.1 MB |
| on_k3 | 1861 | **12397** | **1778** | 9460 | 14.1 MB |
| off_b | 1755 | **12349** | 7723 | 9650 | 14.2 MB |

（單位 MB。資料來源：各 json 的 `memory.launch` / `memory.worst` / `memory.end`）

兩條直接可讀的結論：

- **峰值 12349–12423 MB，四趟幾乎相同（差異 <1%）** ⇒ 與趟數無關、與累積無關
  ⇒ 是「單次載入的固有成本」，不是洩漏。
- **峰值 12.4 GB ≈ 模型 13030 MiB（12.72 GiB）** ⇒ **wired 峰值就是模型權重的 Metal 駐留**
  （Metal buffer 計入 wired，不可換頁）。
- 結束值 1778–7723 MB ⇒ **絕大部分已歸還**（`on_k3` 歸還最乾淨）。

## 二、更正本輪稍早的兩條推導（勿引用）

| 已作廢 | 為什麼錯 |
|---|---|
| 「需要 32 GB 才能判門 1」 | 拿 `swap_used` 當壓力指標。實測每趟起跑 `memory_pressure` 皆 **75–82% free**；且 off_b（第 4 趟）起跑 swap 7650 **>** on_k3（第 3 趟）7459 卻跑出 11.528 ⇒ 「累積到閾值就崩」直接證偽。 |
| 「超訂 4838 MiB（model 13030 + pool 8192 = 21222 on 16384）」 | 這是 **09-24 的舊帳**。`llama-expert-cache.cpp:818` 那段註釋描述的是 P0 之前的狀態；**P0（`CGC_EXPERT_SKIP_READRAW=1`）已砍掉 ~10.9 GiB heap 匿名副本**。本輪 5 趟的 env 確認 `CGC_EXPERT_SKIP_READRAW=1` 已生效。 |

⚠ 但**結論方向沒變、只是換了成分**：新的峰值共存是 `wired 12.4 + pool 8 = 20.4 > 16`
⇒ 超訂 ~4.4 GiB。舊帳的 4.7 GiB（heap 副本）與新帳的 4.4 GiB（pool 換出）**數值接近但成因不同**，
不得混用。

## 三、線 I 已完成清單（避免重複勞動 —— 本輪原建議「掃 pool 曲線」早已做過）

| 項目 | 狀態 | 證據 |
|---|---|---|
| **P0＝L2 單一駐留** | ✅ 已落地且**預設開** | `CGC_EXPERT_SKIP_READRAW=1`（`run_server.sh:354`）；註釋：「砍 ~10.9 GiB 匿名副本＝超訂根因」 |
| **P1／P2（madvise 丟頁）** | ✅ 已落地且**預設開** | `CGC_POOL_MADVISE=2`（`run_server.sh:355`）；本輪 env 確認生效 |
| **熱門優先替換** | ✅ 已落地且**預設開** | `CGC_B_SCHEME=1`（`run_server.sh:356`） |
| **pool 尺寸曲線** | ✅ 已掃（3 點） | `run_server.sh:566-571`，見 TL;DR 第 3 條 |
| 「縮 pool」這條路 | ⛔ **已證偽** | 同上，8→4 慢 36% |

⛔ **一個會產生假結論的坑（已踩過，勿再踩）**：override pool 必須用
**`CGC_SERVER_EXPERT_CACHE_BYTES`**（有 `SERVER_` 前綴）；用 `CGC_EXPERT_CACHE_BYTES`
會被 `run_server.sh` 守門**靜默忽略** ⇒ 兩臂其實都是 8 GiB ⇒ ABBA 得出「4G≈8G」的假結論
（`run_server.sh:573-574` 明文記錄）。

## 四、剩下的真問題與候選方向（未做，供線 I 排程）

既然「加記憶體」與「縮 pool」都被關掉，剩下的槓桿只有兩個方向：

- **(A) 降模型權重的 wired 足跡**（12.4 GB 是最大項）
  - 量化（改模型，非本專案可單獨決定）／降 `ngl`（部分層回 CPU，會慢）
  - ⚠ 兩者都會動到 M1/M2/M3 或輸出函數 ⇒ 屬護欄內的改動，須先過 bit-identical。
- **(B) 讓 pool 頁「換出更便宜」**
  - 目前 pool 是 **malloc 匿名頁** ⇒ 換出要寫 swap、換入要讀 swap。
  - 若改成 **file-backed**（有 backing file）⇒ 乾淨頁可直接丟棄、不用寫 swap；
    髒頁仍有成本，但 pool 內容是權重副本（讀多寫少）⇒ 理論上大部分是乾淨頁。
  - 線索：`run_server.sh:1024-1027` 有 `CGC_MMAP_POOL_FIX`，但它是**探測**
    （判斷 binary 裡有沒有 `pool` 符號），**不是開關** ⇒ 修復是否存在需查 binary。
- **(C)（不推薦）** 縮 pool 到 6 GiB：已有數據 10.48 t/s（-13%），只在「目標機器 <16 GB」
  時才值得重新評估。

## 五、判據（機檢，替換掉 swap）

⛔ **`swap_used` 不得用於**：判記憶體壓力、推算工作集、推算「需要多大記憶體」。
它是**歷史累積量**（不活躍頁換出後賴著不走），且 `purge` 也降不了它（09-22 已記）。

✅ 改用（都存在 json 裡）：

| 指標 | 正常趟 | 崩的趟 | 鑑別力 |
|---|---|---|---|
| `memory.worst.max_wired_mb` | ~12.4 GB（恆定） | 同 | **無**（固有成本，不是訊號） |
| 趟內 `pageins` 增量 | +~170 萬 | `on_k3` +2360 萬 | **14 倍，最乾淨的 thrashing 訊號** |
| `memory.end.pages_wired_mb` | 6051–7723 | `on_k3` 1778 | 歸還程度 |
| `sys_before.memory_pressure` | 75–82% free | 81% free（一樣） | **無**（系統從來不缺記憶體） |
| `memory.worst.min_free_mb` | 14.1–14.2 MB（每趟都一樣） | 同 | **無**（macOS 常態） |

⇒ **唯一能判「這趟有沒有 thrash」的是 `pageins` 增量**。其餘都會給出假的平靜。

## 六、對「這個應用能不能用」的直接回答

- 在 **16 GB** 上：**能用，但 pool 必有 ~4.4 GiB 在 swap 上** ⇒ `cb` 桶是常態成本，
  不是 bug。交付讀數 11.61 t/s 就是在這個狀態下量到的（走廊 10.97–12.02）。
- 要讓它更好，只有 (A) 降模型 wired 足跡 或 (B) pool 改 file-backed —— 兩條都**尚未做**。
- ⛔ 不要對使用者說「請升級到 32 GB」：錯誤診斷，且真因不是總量不足而是峰值共存。

---
產物：本文 ＋ 白皮書 `docs/CGC_POOL_WIRED_BUDGET_20260928_2335.html`。
資料：`Backup/phase_decomp/mtp_kbracket/*.json`（gitignored）。
