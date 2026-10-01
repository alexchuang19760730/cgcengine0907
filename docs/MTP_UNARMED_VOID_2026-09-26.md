# 未武裝的 MTP 讀數：清點與作廢（2026-09-26）

> ⚠ **輸出函數標籤（2026-09-28 依 `MTP_CALIBER_REDEFINE_CHARTER_2026-09-28.md` B2 重標）**：
> 本檔引用的 **12.57／12.62 t/s 屬 MTP-on 輸出**，是**另一個輸出函數**，
> **不可與 MTP-off 互比**（含「MTP 加速 X%」「MTP-on ≥ MTP-off」「以 12.57 為基線／錨點／要打敗的數字」這類表述）。
> 根因：MTP on ⇒ `cparams.n_rs_seq > 0` ⇒ `delta-net-base.cpp:494` recurrent 走 K 槽位回滾，
> **計算份數本身不同**（`gdn` 30→60、`conv_input` 30→150）⇒ 見
> `docs/MTP_BITIDENT_P1_ROOTCAUSE_2026-09-28.md`。本檔既有數字**未改動**。

**一句話**：`spec_type: null` 的產物母體裡，被當成「MTP-on 讀數」引用過的只有 **1 筆**（今天 10:41 的
`/tmp/prodnew_mtp_now/result.json`，pp 275.283 / tg **10.449**），它**作廢**；另外挖出一個方向相反的
誤標 —— **12.57 是「已武裝」的交付 cell，不是 MTP-off** —— 任何把它當 MTP-off 基準的比較（含今天那次
「10.45 vs 12.57」）**一併作廢**。真正未武裝的 MTP-off 生產數字是 **11.03 / 11.79 / 12.20**。

---

## 1. 為什麼 `spec_type: null` 會被誤讀成 MTP-on

`--spec-type draft-mtp` 是 **llama-bench 的旗標**，不是 env。同一台引擎、同一組 MTP env（`CGC_SERVER_MTP=1`
匯出的 `NO_PREFETCH` / `LAYER_CAPS 40-40:256` / `PREFIX_REUSE_CKPT` / `NO_SEQ_RM_PROBE` / `WARM_NPAST`）
在**沒有這個旗標**時，跑的是「有 MTP 的環境、零投機」——draft 不生成、verify 不發生，量到的是那個 env
區塊的價格，不是投機的價格。三條見證可以分辨（本日實測）：

| 見證 | 已武裝（armed） | 未武裝（unarmed） |
|---|---|---|
| 產物 `spec_type` / `spec_draft_n_max` | `"draft-mtp"` / 值 | **`null` / `null`** |
| 印出的 cmd | 含 `--spec-type draft-mtp` | **不含** |
| stderr `SPECDBG round:` 行數 | > 0 | **0** |
| stderr `CGC-WARM verify` 行數 | 24 | **24（一樣）⇒ 這行不是投機的證據** |

第三、四列是關鍵：`CGC-WARM verify n_past=… warm=0 fast=0` 是**暖池閘門**的輸出，MTP-off 臂同樣 24 行。
2026-09-26 早上的判讀曾把這行當成「spec 已武裝」，那是誤讀。

## 2. 清點母體（本日 11:0x 實測）

| 指標 | 數量 |
|---|---:|
| `Backup/` + `/tmp` 下含 `"spec_type": null` 的 JSON 檔 | **143**（早上 `grep -rl` 那一刻是 136；今天幾輪量測加了 7） |
| 其中可解析的 arm 紀錄 | **137** |
| 含 `"spec_type": "draft-mtp"` 的 JSON 檔（已武裝，不在本次作廢範圍） | **134** |
| 137 筆裡 arm env 真的帶 `CGC_SERVER_MTP=1`（＝長得像 MTP-on 的） | **1** |

那 1 筆：`/tmp/prodnew_mtp_now/result.json`（09-26 10:41，tag `prod-new:CGC_SERVER_MTP=1`，pp 275.283、
tg 10.449、逐 rep 12.37 / 9.27 / 9.70，起跑 swap 0、thermal NOMINAL）。

## 3. 作廢清單

| # | 產物 / 主張 | 為什麼作廢 |
|---|---|---|
| V1 | `/tmp/prodnew_mtp_now/result.json`（tg **10.449**、pp 275.283），曾被報告為「MTP-on」 | 未武裝：`spec_type=null`、cmd 無 `--spec-type`、`SPECDBG=0` |
| V2 | 同型的上一筆（`/tmp/prodnew_mtp/`，tg **11.21**、pp **234.35**），重開機時隨 `/tmp` 清掉 | 未武裝（同一個啟動腳本 `Backup/rerun/prodnew_mtp_run.sh` 內**沒有** `--spec-type`）；且產物已不存在 ⇒ 按「無法復現即作廢」處置 |
| V3 | 「**12.57 = MTP-off 基準**」這個標籤（今天的 10.45-vs-12.57 配對、以及任何同型比較） | **方向相反**：12.57 來自 `prod_profile.py` 的交付 cell，那條路徑**自帶** `--spec-type draft-mtp --warm-skip 64`（`prod_profile.py:29,113`）⇒ 它是**已武裝的 MTP-on** 讀數 |
| V4 | 任何把「未武裝 cell」的 t/s 當成 MTP 增益／損失的句子 | 同 V1 機制；本文件即為這類句子的作廢登記處 |

## 4. 不作廢的同族（重要，避免過度作廢）

`spec_type: null` ≠ 無效。**「未武裝」對 MTP-off 臂而言是正確且必要的狀態**，下列都是誠實讀數：

- `Backup/nofill_prod/nf2_fill.json`、`nf_fill.json`、`Backup/mw_ab/mw_ctrl.json` —— MILESTONE_MAP 引用的
  現行生產口徑 **MTP off ＝ 11.03 / 11.79 / 12.20**（三者 `spec_type` 皆 `null`）。
- `prefill250:CGC_SERVER_MTP=0`（2 筆）、`prod25-stream-mtpoff`、`prod25-nail-mtpoff` —— 明示 MTP off 的臂。
- 3 筆 tag 帶 `CGC_MTP_PERF=1` 的 MTP-off 臂（帶了 MTP 的效能儀器，但沒有投機）。
- 134 份 `spec_type: "draft-mtp"` 的產物（已武裝，本次不動）。

## 5. 這是怎麼發生的，以及現在被什麼擋住

- 兩個載體對「開 MTP」的定義不同：`prod_profile.py` **自帶** `--spec-type draft-mtp`；`harness.py bench` /
  `llama_bench_matrix.py` 則是**opt-in**（`--spec-type` 只在有給的時候才附加），而 `CGC_SERVER_MTP=1`
  只是 env、不會自動帶旗標。⇒ 同一句「開 MTP」在兩條線上執行的東西不同。
- **已加的閘（fail-closed）**：`llama_bench_matrix.py` 在任何一臂起跑前檢查「arm 帶 `CGC_SERVER_MTP`（值 ≠ `0`）
  而 cell 沒有 `--spec-type`」⇒ 拒跑並印出修法。`--selftest`：6/6 單元 + 2/2 端到端；把閘改成永遠放行時
  3/6 單元案例變紅（fixture 會紅，非恆綠）。
- **未覆蓋的路徑**：`harness.py bench`（有 `--spec-type`，但沒有這道拒跑閘）與 `arm_two_pass.py`（沒有
  `--spec-type` 傳遞路徑）。這兩條仍可能再造出 V1。

## 6. 出處

- 產物：`/tmp/prodnew_mtp_now/`（V1，已在其目錄蓋 `VOID.txt`）、`/tmp/prodnew_mtpoff_now/`（誠實的 MTP-off，
  但窗口起跑 swap 4937 MiB、跑到 6627 MiB，不可與乾淨窗口的 12.57 併排）。
- 載體定義：`scripts/check/prod_profile.py:29,113`、`scripts/check/llama_bench_matrix.py`（gate 與 selftest）。
- 身分更正：`docs/BITIDENTITY_AND_0907_KNOWHOW_2026-09-22.md`（12.57 的 cell 本來就開著 spec）、
  `docs/MILESTONE_MAP_RECHECK_2026-09-25.md`（現行 MTP off ＝ 11.03／11.79／12.20）。
