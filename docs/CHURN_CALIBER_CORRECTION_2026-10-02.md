# churn 口徑更正：S3（40 段→1）的前提是 **20.2%**，不是 **42.3%**

日期：2026-10-02　｜　線：A　｜　性質：**既有資產的更正，不是新實驗**（0 重建、0 新 run）

---

## 1. 一句話

判死 S3 的那個 **42.3%** 是 **`ntok=4`（MTP-on verify 桶）** 的讀數；
**交付口徑（`ntok=1`、MTP off、主 context）** 的同一量是 **20.2%**（entry 粒度 **3.4%**）。
**殺卡依據被高估 2 倍以上。**

## 2. 出處（全部已在樹上）

| 項 | 位置 |
|---|---|
| 立項卡 | `scripts/check/charters/exp-churn-delivery-630.yaml`（09-28，線 A） |
| 實跑 | `Backup/exp_runs/exp-churn-delivery-630_20260928_231234.json`、`Backup/churn_delivery_630_2026-09-28/` |
| 讀數工具 | `scripts/check/premise_b_read.py -v <log>` |
| S3 的 ABBA | `Backup/seg_batch_s1_pairs/abba_212809.json` |

## 3. 讀數（`premise_b_read.py` 對 09-28 的 log）

```
churn 20.2%  (3026/14976 publishes = 78/384 layer-steps)  coverage 99.7%
★ churn BY ntok:
    ntok=1:  20.2%  (3026/14976 publishes)   main context, MTP off -- THIS is the delivery decode step
✓ ntok=1 is the main context with no draft calls in this log: this IS the delivery decode step
ENTRY granularity: overall 4014/119808 = 3.4%  (ntok=1 同值)
```

- **量具活著且歸屬正確** ⇒ 卡片 falsify 條件的第二支（「只有 draft 桶」）不成立，這是一個**真讀數**。
- 兩刀粒度：**publish 粒度 20.2%**（78/384 個 layer-step 有 ≥1 個被消費的 id 移動）；
  **entry 粒度 3.4%**（119808 個被消費 id 中只有 4014 個真的移動）。

## 4. S3 是什麼，值多少

臂：`prod-new:CGC_SEG_BATCH=1;CGC_B_SCHEME=1;CGC_SLOT_TABLE_GPU=1`
（`llama-context.cpp:5526` 的 one-line gate 已落地 ⇒ 不用重建）
ABBA（A,B,B,A,A,B,B,A）、同 cell（prod-new / p2048 / g128 / d512 / b5632）：

| | t/s | pp |
|---|---|---|
| A（41 段） | **12.2015** | 275.39 |
| B（S3，40 段→1） | **22.2** | 296.24 |

⇒ **這是唯一在認可形狀上實測越過 20 t/s 的路徑**（+82%）。

## 5. 判詞狀態（需要 operator 裁決）

卡片的預註冊規則是：`churn = 0 ⇒ S3 前提復活；> 0 ⇒ 22.2 那條路關閉`。
實測 **20.2% > 0** ⇒ **按字面規則 S3 仍關閉**。

⚠ 但規則是**在「預期 0」的前提下**寫的，而它要防的風險（表映射在步內移動）現在量到的是
**publish 粒度 20.2%／entry 粒度 3.4%**，而且**殺卡的那個 42.3% 被證明是另一個口徑**。
卡片自己也記了「**禁止把 42.3% 當常數**：同一 signature 在 43 個舊 run 之間跨 37.2%–84.4%」。

⇒ **這是一個判斷點，不是一個算術點**：S3 值 +82%，代價是 20.2% 的步內映射移動（需要兜底）。
**本文件不自行改判**，只把更正擺出來。

## 6. 與本軸的關係（三塊拼圖現在齊了）

1. **瓶頸**＝每步 ~633 個 command buffer 的空檔，`wait` 佔 decode 步 84.8%（2026-10-02 DECPROF 實測復現）。
2. **唯一越過 20 的路**＝S3（削序列化：40 段→1 段）＝ 22.2 t/s。
3. **殺 S3 的依據**＝42.3%，實為另一口徑、且**高估 2 倍以上**。

⇒ 也就是說：**在「削序列化」這條軸上，唯一有正讀數的臂是被一個高估的數字殺掉的。**

## 7. 還沒做的（本軸剩餘的兩個缺口）

1. **`idle 是否為每步常數`** —— `docs/DECODE_STEADY_BASELINE_2026-09-19.md` §3 的「下一步 #1」，
   至今只在 bench 形狀上量過。需在**穩態**（`http_duo.py`）上重測 ⇒ 決定 25 t/s 有沒有第二條路。
2. **`CGC-GPUNODE` 的 `layer gpu_sum` populate** —— 本日實測印 `0.00`、`(NO TIMESTAMPS)` 路徑未被走，
   ⇒ 空轉目前是 **DECPROF wait ＋ ioreg util 兩支儀器併出來的**，不是單一儀器直讀。
   要坐實需動 `ggml-metal`／`ggml-backend`（B 線工作面附近，需協調）。

---

## 8. 【當日追加】重測 S3 的結果：**臂已被改掉，重測在結構上不可能**

2026-10-02 10:08，`CGC_INTERNAL_CALL=1 python3 scripts/check/seg_batch_abba.py --pairs 2 --warm-skip 64`
（cell 與原件同）。8 趟全 rc=0。產物 `Backup/seg_batch_abba/abba_100749.json`。

| 臂 | tg（四趟） |
|---|---|
| A（41 段） | 11.596 / 10.783 / 10.803 / 10.621 |
| B（S3） | 11.184 / 9.951 / 10.287 / 10.654 |

表面上是 **A ≈ B（B 略慢）** ⇒ 看起來「22.2 不復現」。

**但這是假象。原因在原始碼裡**（`ggml-backend.cpp:1817-1826`）：

> `[CGC 2026-10-01 P2 engine-surgery]` **`CGC_SEG_BATCH=1` now deliberately falls through into the
> DEFAULT 41-segment wait->callback_eval->submit loop below**（the proven correct async path）.
> The old shortcut that submitted the whole graph in one async compute with no hook
> (stale ids -> wrong output) has been **removed** so SEG_BATCH is a deliverable arm.

⇒ **今天 `CGC_SEG_BATCH=1` 等於 41 段循環** ⇒ 我測的是「41 段 vs 41 段」。
⇒ **22.2 t/s 對應的那條程式路徑已在 10-01 被移除**，這個臂再也量不到它。

### 8.1 這改寫了本文件 §5 的性質

§5 說「S3 的值是 +82%，判詞需要 operator 重裁」。更準確的說法是：

- **22.2 t/s 是真的**（分段本身確實值 ~+82%），
- **但它從來不是可交付的**——它的輸出是錯的（stale ids），10-01 的 P2 手術就是為此把它移除的；
- **而且它拿不回來**：每段之間的 ensure（駐留）是**必需**的，舊捷徑跳過它就輸出錯。
  ⇒ 與 2026-10-02 的 L20-1（我）、B 線的 S1SS **三方收斂**：單提交撞的是**駐留牆**，不是映射或提交數。

### 8.2 因此正確的軸是

**不是「移除分段」，而是「讓每段的 wait ＋ hook 更便宜或重疊」。**
DECPROF 實測 decode 步：`wait` 85%、`cb` 7 ms、`submit` 3.8 ms ⇒ 空轉坐在那 85% 的 wait 裡。

### 8.3 要修的資產（過時工具）

`scripts/check/seg_batch_abba.py`（09-24）的 docstring 仍寫
「Arm B … submits the whole graph in one async dispatch」——**那個行為 10-01 就沒了**
⇒ 任何用它跑出來的 A/B 都是空的。**跑前必查臂是否還存在。**

### 8.4 零碼重價「等待序列化」的既有旋鈕

`ggml-backend.cpp` 同一段註解載明兩個 **diagnostic** 開關：

- `CGC_SUBMIT_AHEAD=1` — restores the racy submit-ahead order（拿掉「等 segment[i] 全完才 hook」的序列化）
- `CGC_TOPK_BOUNDARY=1` — reverts to the buggy topk-VIEW boundary

兩者輸出都是錯的，但**不需要重建**就能重價「那段等待值多少」——這正是 22.2 想回答的問題。
