# rep-split 認證協定：一個 rep 一次啟動、之間冷卻（2026-09-28）

> 目標：讓同一支臂的三個 rep **不再互相污染**，且每個 rep 的來歷在產物裡可查。
> 實作：`scripts/check/rep_split.py`（session 編排）＋ 測試卡 §2.5 的孿生 cell
> `delivery-repsplit`（合約側，`cell_contract.py`）。

## 0. 一句話

`llama-bench -r 3` 的三個 rep 在**同一個行程**裡跑，而它們**不可交換**：實測 rep1 活的同時
rep2/rep3 的 draft 鏈整個死掉、量到純解碼，而臂仍被讀成「k=3」。這個協定把「三個 rep」換成
**三次啟動、每次一個 rep、每次啟動前必須回到 NOMINAL**，並把「三次都通過同一組規則」變成
**拿到數字的必要條件**。

## 1. 為什麼（量到的事實，不是偏好）

| 事實 | 出處 |
|---|---|
| `--delay` 冷不到 rep 之間 —— 它在 reps 迴圈**之外**（`llama-bench.cpp:3353` vs `:3434`） | 讀碼 |
| `cell_contract` **明文拒 `reps=1`** ⇒ 舊口徑下「rep 之間冷卻」不可表達 | `cell_contract.py` selftest |
| 同一支臂 rep1 活（`drafted=90`）、rep2/3 全死（`drafted=0`、`mean_len=1.0000`） | `docs/MTP_DRAFT_FIRST_STEP_2026-09-28.md` |
| 一支 77 秒的臂讓 swap 長 2.8 GB、free 觸底 16.8 MiB | `docs/ARM_DRAFT_LIVENESS_2026-09-28.md` |
| corpus 裡唯一沒出現過死鏈的 k 結論，正是**一個 rep 一次量**的形狀（21 個讀數、`mean_len` 2.21–2.97 全活） | `Backup/k_abba_2026-09-23/abba/driver.log` |

## 2. 這個協定強制什麼（每條 fail-closed）

1. **每次啟動前必須 NOMINAL**，等不到（超過 `cool_max_s`）就**拒跑**。
   與 `thermal_pressure.wait_nominal` 的其他 caller 相反（它們可以「照跑、但標記不可引用」），
   這裡 `ok=False` 就是拒絕理由 —— 熱啟動正是要移除的污染。
2. **每次啟動恰好 1 個 measured rep**，且**必須是宣告過的孿生 cell**（見 §3）。`--shape` 內若出現
   `--reps` 直接拒收。
3. **逐 launch 驗證**：產物要有**恰好 1 個** sample、`spec_draft_n_max` 讀回來要等於要求的 k、
   而那**一個** measured rep 的 draft 鏈必須活著（`draft_liveness.verdict(expect_timed=1)`）。
4. **任何一次啟動失敗 ⇒ 整個 session 拒絕**，**不寫出部分 arm**。理由寫在拒絕訊息裡：一個 arm
   的三個 rep 必須全部通過同一組規則，否則它不是那個 arm 的三個樣本，而是混合體 —— 而半支臂
   比沒有臂更危險，因為它看起來能用。

## 3. 合約怎麼處理（重點：`reps` 沒有被放寬）

`reps` 是兩個權威 cell 的**嚴格維度**，維持不動。新增的是**孿生 cell**：

```json
"delivery-repsplit": {
  … 與 delivery 逐項相同 …,
  "reps": 1,
  "rep_split": { "of": "delivery", "launches": 3, "cool_to": "NOMINAL", "cool_max_s": 420 }
}
```

`cell_contract.validate_rep_split()` 對**每一個** `rep_split` 宣告驗這幾件事（任何一項不過 ⇒ 整個
合約 `ContractError`，且在 `resolve_cell` 裡跑 ⇒ 所有路徑都驗）：

- `of` 必須指向**存在**的 cell（含 `(default)`）；
- `launches` 是 ≥1 整數、`cool_to` 只能是 `NOMINAL`、`cool_max_s` > 0；
- 孿生的 `reps` **必須是 1**（= 這個 cell 的定義）；
- **孿生只准與 base 差 `reps`/`rep_split`**（其他維度任一不同 ⇒ 拒）；
- `launches × 1` 必須**等於 base 的 `reps`** ⇒ 孿生不能用來量比較少的 rep。

已驗的兩個方向：

```
cell 口徑校驗通過（cell=delivery-repsplit，嚴格維度 15 項一致）。
reps=1 用預設 cell 名 -> 拒        # 沒被放寬
delivery 用 reps=1  -> 拒          # 沒被放寬
```

`cell_contract` selftest **15 → 30 項**（含 8 個「壞宣告必須被拒且指名原因」）。

## 4. 成本（量到的，不是估的）

真實 session（`delivery-repsplit`、`prod25-stream`、k=3、3 次啟動）：

```
   launch  rc   wall_s   cool_s  swap_before  swap_after  sample
        1   0     41.1      0.0         9733        9935  [7.77115]
        2   0     40.6      0.0         9935        9946  [9.83074]
        3   0     40.4      0.0         9946        9914  [9.86039]
  measure 122.1s + cool 0.0s = 122.3s session wall
```

三個數字值得單獨講：

- **冷卻是 0 秒，三次都是。** 每次啟動**結束時**仍是 `NOMINAL`。⇒ 舊協定壞掉的原因**不是熱**，
  這與 `ARM_DRAFT_LIVENESS` 的結論一致（死鏈綁在第二個 rep，與溫度無關）。這個協定的成本因此
  **不在冷卻**，而在下面那一項。
- **成本是重付載入**：單次啟動 ~41 秒，而 `-r 3` 的同一支臂在一次啟動裡約 73 秒
  （`MTP_DRAFT_FIRST_STEP` §4 的實測）。⇒ 這個形狀的代價約 **+49 秒／arm（+67%）**，全部是
  （N−1）次重付的模型載入＋expert-cache 暖機＋`-d 512` depth prefill。`rep_split` 區塊把
  `launch_wall_s`／`cool_wait_s`／`session_wall_s` 都寫進產物，所以「貴多少」是可引用的。
- **第一個 launch 的樣本明顯低**（7.77 vs 9.83 / 9.86）—— 見 §6，這是新協定自己暴露出來的
  順序效應，不是它造成的。

`--cool-max-s` 可以**調低**（更容易拒跑），但**調低不會產生熱啟動** —— 這是有意的：可以接受的
方向只有「更嚴格」。

## 5. 形狀：合併紀錄是**既有 loader 認得的形狀**，不是新口徑

`rep_split.py` 把 N 次單 rep 啟動合成**一份 matrix 形狀的 arm 紀錄**（`rows[].samples_ts` 依啟動
順序），所以下游不必改：

```
k_swing_decompose.arm_rows('/tmp/rs_k3/arm.json')      -> [7.77115, 9.83074, 9.86039]
draft_liveness --log-dir /tmp/rs_k3 --expect-timed 3   -> rc=0「every measured rep ran the draft chain」
驅動的 k 讀回                                            -> 3（要求 3）
```

**順帶修掉一個真缺陷**：`draft_liveness --log-dir` 原本展開成**非遞迴**的 `<dir>/*.stderr.log`，
對 `<arm>/launches/L01/…` 這種（本協定的）佈局會回 `READ FAILED: no log matched` —— 而它自己的
`--help` 寫的是「inside it」，模組裡也早就有會走樹的 `find_logs`（只給 `--audit` 用）。現在
`--log-dir` 走樹，且 selftest 多一條把這個佈局釘住。

## 6. 這個協定**暴露**的新問題（尚未處置）

**三個 measured 樣本不是同一個分佈**：`7.77 / 9.83 / 9.86`。第一個 measured launch 顯著偏低
（−21%），後兩個幾乎相同。合理假設是**第一個啟動仍在付 OS page cache / expert cache 的冷啟成本**
（`swap_before` 序：9733 → 9935 → 9946，第一個啟動把 swap 長了 202 MiB）。

**這是 n=1 的觀察，不是結論**，但兩件事可以現在說：

- 這個協定**把它記下來了**（`rep_split.evidence[].samples_ts` 逐 launch、含順序），所以下一個
  session 就能判定它是系統性的還是雜訊。
- 若它是系統性的，處理方式有兩個（都還沒做）：**(a)** 在配對層用 **ABBA 順序**（repo 的
  `ab_interleave.py` 與 09-23 的 ABBA 先例），讓「第一個」這個位置被兩支臂輪流承擔；
  **(b)** 一個**明確標記為不進證據的暖身啟動**（`--prime`），讓三個 measured 啟動起跑時狀態等價。

## 7. 這個協定**沒有**修掉的

- **生成中的 `X == Y` 那一類**（每個 launch 仍約 12% 的回合損失，log 裡是 2×`-1`）：
  它不毀臂（回合損失而已），但仍是同一族的殘餘，見 `MTP_DRAFT_FIRST_STEP_2026-09-28.md` §5。
- **單一 rep 內部的退化**：`-r 1` 沒有「第二個 rep」可以污染，但一個 rep 內仍有 41 個回合與
  64→128 token 的生成；`draft_liveness` 判的是這個 rep 整體（`mean_len` 的**最小**值），
  所以「前半活、後半死」這種形狀仍然只會被判成「這一 rep 有問題」。
- **`prod-new`（預設）cell 沒有孿生**：本輪只宣告了 `delivery-repsplit`。要用在別的 cell 上，
  得先宣告它的孿生（`validate_rep_split` 會強制「只差 reps/rep_split且 launches×1 == base reps」）。

## 8. 用法

```bash
# 一支臂 = 一個 session（3 次啟動、之間等 NOMINAL）
python3 scripts/check/rep_split.py \
  --cell delivery-repsplit \
  --arm "prod25:!CGC_PREFILL_STREAM=1;!CGC_GATHER_SLAB_CAP=256" \
  --charter scripts/check/charters/exp-k3-pair-cert.yaml \
  --shape "--prompt 0 --batch 512 --ctx-size 4096 --warm-skip 64 --fixed-fill-seed 0 \
           --spec-type draft-mtp" \
  --k 3 --out /tmp/kb/r1_b_k3 --json /tmp/kb/r1_b_k3/arm.json

python3 scripts/check/rep_split.py --dry-run …   # 零 GPU：只印計畫與每次啟動的命令
python3 scripts/check/rep_split.py --selftest    # 14/14（不需 GPU）
```

`scripts/check/k3_pair_cert.sh` 的 `arm()` 已經改走這條路（`CELL=delivery-repsplit`），所以
「四對 pilot」會自動使用這個協定；`--expect-timed "$REPS"`(=3) 與三份單 rep log 逐項對得上。

## 9. 驗證狀態

| 項目 | |
|---|---|
| `cell_contract.py --selftest` | 30/30（含孿生的 8 個壞宣告案例） |
| `rep_split.py --selftest` | 14/14（含「合併紀錄被 `k_swing_decompose.arm_rows` 讀得動」的跨模組證明） |
| `draft_liveness.py --selftest` | ok（新增「`--log-dir` 走子目錄」案例） |
| 真實 session | 3/3 啟動 rc=0、6/6 個 phase 活的、合併紀錄 3 個樣本、liveness 閘 rc=0 |
| `k3_pair_cert.sh` | `bash -n` 通過；逐臂驗證（k 讀回＋liveness）在新產物上通過 |

**未做**：四對 pilot 沒有跑（需要 ~4 個 session×2 支臂＋配對順序的設計）；§6 的順序效應未處置。
