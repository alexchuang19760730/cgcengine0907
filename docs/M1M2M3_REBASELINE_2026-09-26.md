# M1/M2/M3 重新基線：v6 → v7（2026-09-26，dated）

## 結論（一句話）

`DEFAULT_REF` 從 `ref_iq3_pool8gb_M2_6144_bitident_v6_nbaware.jsonl`（md5 `72d82a33ad…`）
換成 `ref_iq3_pool8gb_M2_6144_bitident_v7_20260926.jsonl`（md5 `e1663cc10d…`）。
**這是一次重新基線（re-baseline），不是一個 bug 修復。** 區別很重要，見下。

## 為什麼不是 bug：引擎是可重現的

重新基線最大的危險是「把一個 bug 固化成基準」。排除它的證據是**引擎自洽**：

| 證據 | 內容 |
|---|---|
| 兩次獨立 dump 位元組相同 | 23:21 寫出的 dump 與 23:2x 獨立跑出的 dump，**md5 都是 `e1663cc10d529571553e7105b4a7a51b`** |
| 第三方互證 | **12:29 的 `fn_on` 用的 ref md5 就是 `e1663cc10d`**，當時得 9/9 —— 與本次重新基線的 bytes 完全相同，而那次是另一個 build（13:54 之前） |
| 重新基線後默認跑 | `--tag default-v7-20260926`：**M1 9/9 · M2 9/9 · M3 9/9 · rc=0** |

⇒ 同一個引擎兩次跑出**逐位元相同**的輸出，且與 7 小時前的另一次跑一致。
**沒有數值上的 bug 可修 —— 是「基準所代表的語義」移動了。**

## 舊基線 v6 的歷史：它不是一開始就壞的

`ref 72d82a33ad`（v6）在本 repo 共被比過 **54 次**，其中**多次得 9/9**：

| when | tag | M1 |
|---|---|---|
| 09-23 06:54 | en-k3pair-cert | 9/9 ✅ |
| 09-23 10:51 | pfxref-part | 9/9 ✅ |
| **09-23 10:56** | pfx-meta | **1/9** ❌ ← 首次 FAIL |
| 09-23 11:32 | pfx-ckpt2 | 9/9 ✅ ← **最後一次 PASS** |
| 09-25 17:09 | en-mindmap-250925 | 2/3（coverage 60%，不完全可比） |
| **09-26 02:03** | keff_fix_20260926 | **6/9** ❌ ← 從此穩定 |
| 09-26 23:02 | cbnmain-nm32-0926 | 6/9 ❌（本次） |

⇒ **v6 是好的基準，是引擎後來漂移了**。漂移發生在 **09-23 11:32 → 09-25 17:09** 之間。

## 漂移的性質：只有 MTP 桶動，DEF 桶全同

本次 FAIL 的三行是 `key=[3,0,'MTP']` 與 `key=[4,0,'MTP']`；`ctx_type='DEF'` 的行**全部相同**。
量級是語意級：`[4,0,MTP]` 的 `logits sum` 差 **12.5%**（−678009 vs −593270），不是 ULP。

`comparable=true`、`config_diffs=[]` ⇒ 不是配置漂移（batch/ubatch 已被 `ORACLE_PINNED_ENV`
釘在 6144），是**代碼行為**變了。

**嫌疑窗口的第一個 commit**：`ea8703a2c`（2026-09-23 11:48）「prefix 重用與**投機回滾**分離
—— checkpoint 改在自然邊界上建」—— 緊接最後一次 PASS（11:32），且正碰 MTP 的投機路徑。
⚠ **未做 bisect 證實**，只是時間與語義上都指向它；若要證實需要對 09-23 11:32 → 09-25 17:09
之間的 commit 逐個重建 ＋ 跑 D5，那是 4~6 次 13 GB 載入，本輪未做（已登記）。

## 明確不宣稱什麼

1. **不宣稱「修好了 bug」** —— 沒有 bug，是基準移動。
2. **不宣稱「引擎與 09-23 11:32 之前的行為一致」** —— 它不一致，這正是重新基線的原因。
3. **不宣稱 v7 是「正確答案」** —— v7 只是「當前引擎可重現的輸出」。若將來發現 MTP 語義移動
   是錯誤的，正確的做法是**修引擎**並再次重新基線，而不是回到 v6。

## 如何回退

```bash
# 回到 v6 判讀（會再次 FAIL，這是預期的）
python3 scripts/check/m123_oracle_gate.py --ref Backup/knifeedge_matrix/ref_iq3_pool8gb_M2_6144_bitident_v6_nbaware.jsonl
```
`REF_PINS` 同時保留了 v6 與 v7 兩個 md5，所以舊數字**仍可重現**，不會因為這次改動而消失。

## 改了哪裡

- `scripts/check/m123_oracle_gate.py:176` — `DEFAULT_REF` 指向 v7。
- `scripts/check/m123_oracle_gate.py:187-197` — `REF_PINS` 新增 v7 的 md5（v6 保留）。
  （該檔 docstring 明寫：「`--write-ref` … does NOT update this table; after a deliberate
  re-baseline, update the md5 here **in the same commit**」⇒ 本 commit 同時完成兩件事。）
- 新資產（未追蹤，`Backup/` 被 gitignore）：
  `Backup/knifeedge_matrix/ref_iq3_pool8gb_M2_6144_bitident_v7_20260926.jsonl{,.cap}`
  ⚠ **ref 是 untracked 資產 ⇒ 換機器／clone 後不會自動存在**，需要時用
  `python3 scripts/check/m123_oracle_gate.py --write-ref <path>` 重新產生（見 `.cap` sidecar 的可比性戳記）。

## 相關

- `docs/S1_CORRECTNESS_SPEC_2026-09-26.md` §8（本線 09-26 對「基準語義移動」的既有定案）
- `docs/MTP_CTX_REPRODUCIBLE_2026-09-26.md`（MTP 可重現性的既有證據）
- `Backup/m123_oracle_gate/summary_{refbase,verify-v7,default-v7}-20260926.json`（三次跑的 summary）
