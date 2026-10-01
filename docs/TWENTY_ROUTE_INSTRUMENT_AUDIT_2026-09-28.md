# 到 decode 20 的三個判詞：量具 / 版本 / 還活著嗎（2026-09-28）

性質：**唯讀稽核。零 GPU、零重建、零 `src/` 更動。** 稽核期間盒子有一支**外來的**
`llama-bench`（PID 9677，`llama_bench_matrix.py --arms prod-new:CGC_SERVER_LAYER_CAPS=40-40:16;…`
→ `Backup/phase_decomp/mtp_kbracket/off_a.json`）在跑，**未被干擾、也未與它並跑**。

方法沿用 `TWENTY_ROUTE_VERDICT_AUDIT_2026-09-28.md`，但問得更深一層。上一輪問「**引用**對不對」，
這一輪問「**量具**還在不在」——同一個數字三問：

1. **量具是什麼**（程式碼位置／計數器／探針／估計式），以及它在哪個入口下被驅動；
2. **量具的版本**：那個數字取得之後，量具本身有沒有被改過（`git log -S` 對 `run_server.sh`）；
3. **它現在還輸出嗎**：原始碼在？工具在且 selftest 綠？原始產物還在磁碟上？

**這三問是本 session 第五、六、七次抓到同一族病**（前四次：`ffn_moe_missmask` 被誤判成 leaf、
`premise_b(c)` 說 NOT APPLIED 實則已落地、`S2_OVERLAP §5.3` 的汙染理由不成立、`M-S2 判不可達` 是誤引；
第五次是 `ntok=1` 的 draft 桶被讀成交付步）。

---

## 0. 結論表

| 判詞 | 被引用的數字 | 量具 | 版本 | 活著？ | 可否引用 |
|---|---|---|---|---|---|
| **(i) ρ/prebind** | `+14.0~14.5%` | **無量具**（`cb=42.04` 定價模型） | 白皮書 09-24 | — | ⛔ 是模型上界，非量測 |
| | `+4.7%` | ABBA（哪個入口見 §1） | 09-23 | 原始碼活著、產物未複核 | ⚠ **繞過交付入口**，且基準是 MTP on |
| **(ii) leaf 裝置化** | `576/576` | 逐層 `fnv1a64` 指紋 ＋ `answer_md5` | 09-15 起 | ✅ **活著**（`llama-context.cpp:4386`；6 份產物在） | ✅ 但**無速度主張** |
| | `22.22 t/s` | 自製 ABBA over `llama_bench_matrix.py` | 09-24 | 開關 09-25 才可武裝 | ⛔ **入口不合規**（本輪最重要的更正） |
| | `19.0 / 17.36 / 22.9` | **無量具** | 09-24 | — | ⛔ 解析估計，§8 自陳推導未重建 |
| **(iii) 便宜 hook** | `12.86 ms（~8%）` | `cb_miss_regression.py` ＋ 迴歸 | 09-20 | ✅ 工具在、10/10 PASS、產物在 | ⚠ 它是 **post-hoc 截距**，已被降級 |
| **閘門本身** | `±11.3% 噪聲底` | 控制臂漂移（`analyze.py`，逐實驗副本） | 09-26 | 產物在 | ⛔ **不可重現**（重算 9.23%），且輸入兩個都是 thermal HEAVY |

---

## 1. ★ 系統性發現：launcher 白名單的「晚到」，比任何單一 bug 貴

`run_server.sh` 只轉發**它白名單裡**的環境變數；**沒列到的會被靜默丟掉**（這個陷阱 repo 自己記了
不只一次，見 `:1680`、`:2513`、`1710`）。所以有一條**可判定的規則**：

> 若某個開關**第一次**進入 `run_server.sh` 白名單的日期，**晚於**某個數字被量測的日期，
> 那麼那個數字**必定是繞過交付入口**產生的（引擎能吃該 env，但 launch 路徑傳不進去）。

`git log -S<knob> --reverse -- scripts/run_server.sh`（歷史 638 個 commit、**非 shallow**）：

| 開關 | 首次進白名單 | 依賴它的數字 | 量測日 | 判定 |
|---|---|---|---|---|
| `CGC_SLOT_TABLE_GPU` | `0bd1dacd8` **09-15** | 576/576 探針臂 | 09-17/18 | ✅ 入口合規 |
| `CGC_S1_TABLE_CHURN` | `e627de2c2` **09-16** | premise B 的 churn | 09-20 | ✅ 入口合規（但**口徑是 prod25**，見 §3） |
| `CGC_RHO_PREFETCH_MAXQ` | `079f2fe46` **09-23** | ρ 的 MAXQ 保險絲 | 09-23 | 邊界 |
| **`CGC_SEG_BATCH`／`CGC_B_SCHEME`** | `356d6c298` **09-25** | **22.22 t/s（全案唯一越過 20）** | **09-24** | ⛔ **早一天 ⇒ 繞過** |
| `CGC_RHO_PROBE`／`CGC_MISS_MASK` | `591d7cfac`／`58e0f4020` **09-26** | ρ 的 A/B | 09-23/24 | ⛔ **繞過** |

程式碼註解自己承認了後兩條：`:1710`「the engine has read both since 2026-09-23/24, but **no entry here
ever forwarded them** — so the only A/B that ever measured rho **had to bypass this launcher entirely**」；
`:2517`「在它之前，S1 **只活在 `llama-bench` 路徑**（那裡直接吃 env）⇒『在交付載體上驗收 S1』根本跑不起來」。

### 1.1 ★ 這修正我上一輪說錯的一句話

我上一輪寫「**唯一在認可形狀上越過 20 的讀數：單段提交 22.22（同 cell 同 build，ABBA）**」。
**cell 相同是對的，入口不是。**

```
abba_212809.json     plan=[A,B,B,A,A,B,B,A]  8 records
  cell  = {profile: prod-new, prompt 2048, gen 128, depth 512, batch 5632, warm_skip 64, reps 3}  ✅ 與權威 cell 同形
  A 臂（4 次） 12.2015 / 11.9649 / 9.3159 / 11.7144        extra_env = {}
  B 臂（4 次） 22.2222 / 20.2957 / 20.0574 / 20.3347        extra_env = {CGC_SEG_BATCH:1, CGC_B_SCHEME:1, CGC_SLOT_TABLE_GPU:1}
  產物        res_*.json 是 **`llama_bench_matrix.py` 的 row 形狀**（`tag`/`profile`/`extra_env`/`env`）
  時間        2026-09-24 21:19–21:28   ← 早於 `CGC_SEG_BATCH` 進白名單（09-25）
```

⇒ 它是 **`llama-bench`／matrix 入口**的產物，**不是 `harness bench`**。在你立的規則下
（「用 `prod_new` profile ＋ `harness bench` 作為數據依據」），**它不合格**。

**好消息是這條路現在才第一次真的可跑**：`CGC_SEG_BATCH` 09-25 進白名單，`CGC_SLOT_TABLE_GPU` 09-26，
所以「在交付載體上驗收 S1」是 09-25 之後才成立的事。**重跑不是「便宜的重複」，是新可用的量測。**

---

## 2. (i) ρ/prebind —— 量具活著，但它量的是另一個輸出函數

**數字**：`14.39 t/s（+14.5%）` 是白皮書在 `cb = 42.04` 下的**定價模型輸出**；
`GAP_FIX_EXEC §3` 自己寫「白皮書 §6 那張敏感性表（33% → 13.53 … 100% → 16.01）**沒有一個 h 有實測支撐**」，
且 **h ≠ ρ 的覆蓋率 0.854**（可以同時 `cov = 0.854` 而 `h ≈ 0`）。⇒ **這一格沒有量具。**

`+4.7%`（`RHO_MAXQ_SETTLED` 09-23：rho-q16 **9.64** vs on **9.21**）**有**量具，但三個問題：

1. **入口**：ρ 需要 `CGC_RHO_PROBE` ＋ `CGC_RHO_FILL`（`:4525`／`:4616`／`:6545`），而這兩個
   **09-26 才進白名單** ⇒ 09-23/24 那次 A/B **繞過 launcher**（程式碼註解自陳，見 §1）。
   白名單的 `CGC_RHO_PREFETCH_MAXQ`（09-23，`079f2fe46`）只封**佇列深度**，單獨不構成 A/B。
2. **基準**：`+4.7%` 是對 **`on`（MTP on，9.21）** 算的，而**同一格的 base（MTP off）是 9.85——比它快**。
   交付口徑（MTP off）下 ρ 的淨增益**沒有量過**。
3. **它的量具本身是探針**：`:1722` 註明 `CGC_RHO_PROBE` 每層付一次**同步讀回**
   （`ggml_backend_tensor_get`，`n_expert × n_tokens` floats × 40 層/步），並明文
   「**never quote throughput from an arm that has it on alone**」。

**活著嗎**：原始碼在（`llama-context.cpp:4525/4616/6545`、`llama-expert-cache.cpp:1755/1790`），
env 現在可從 `--arms` 武裝。**這一格現在第一次可以在交付載體上量。**

---

## 3. (ii) leaf 裝置化 / M-S2 —— 三個數字、三種性質，只有一個有量具

| 數字 | 性質 | 量具 | 活著？ |
|---|---|---|---|
| **576/576** | 實測（數值身分） | 逐層 `fnv1a64` 指紋 ＋ `answer_md5` | ✅ **量具活著** |
| **22.22 t/s** | 實測（形狀） | 自製 ABBA over `llama_bench_matrix.py` | ⚠ 開關 09-25 才可武裝 ⇒ **入口不合規** |
| **19.0 / 17.36 / 22.9** | 解析估計 | **無** | — |

**576/576 的量具**：`llama-context.cpp:4386` 印 `CGC-S1: OUT phase=… il=… ne=[…] fnv1a64=…`，
env 是 `CGC_S1_OUT_CAP`／`CGC_S1_OUT_LAYERS`（**在當前白名單**，`run_server.sh:1530` 的迴圈），
消費者為 `scripts/check/m123_oracle_gate.py`（1386 行）與 `decode_carrier_ab.py`。
**產物在磁碟上**：09-18 11:37–11:42 有 **6 份** log 帶指紋列（各 96 行 `CGC-S1: OUT`）。

**19.0／17.36／22.9 沒有量具**，這是本節最要緊的一句：它們是 `GAP_FIX_WHITEPAPER §4.C` 的**手算分解**
（`total → union` 22.9、只有 submit 能搬 19.0、B 的 slot 間接化 17.36），而該篇 **§8 自己列為未閉合**：

```
⚠ 分量相加在這個 regime 不閉合：gap ⊆ cb + submit 的交叉檢查以 10.09 ms 失敗
未閉合項：M-S2 的 19.0 與 B 的 17.36 口徑差未閉合；19.0 的推導過程未在本篇重建
⇒ 現階段自評「上界 ≤ ~5%，且不可引用」
```

⇒ 所以「M-S2 判**不可達**」（`S1_CORRECTNESS_SPEC §4`）是**把一個沒有量具的估計當成判詞**，
而上一輪已證明那是誤引（原判是「不可引用 ＋ 押後」）。**這一條的量具問題比上一輪看到的更根本：
它連一個可量的對象都還沒決定。**

---

## 4. (iii) 便宜 hook —— 量具活著，但那個 12.86 是 post-hoc 迴歸的截距

**量具全在**：

```
scripts/check/cb_miss_regression.py                604 行，--selftest PASS
Backup/cgc_logs/f1_cb_miss_regression_report.json  ✅
Backup/cgc_logs/llama_server_20260920_114450.log   ✅（輸入：3921 個 (step, layer) 樣本，跨 216 步）
Backup/phase_decomp/F1_CB_MISS_REGRESSION_20260920.conditions.md ✅（事先註冊）
```

**但那個數字不是它判定的東西。** F1 的**預先註冊規則**判的是：

```
VERDICT (full set, n=3921) : LINEAR        ← 邊際 miss 成本 0.695 ms（bandwidth bound）
```

而 `12.86 ms/step` 來自同一節的 **§1.3 post-hoc 模型比較表**（`cb = 0.46 + 0.695·m`，R² = 0.8617）——
F1 自己在那一節寫著「**未**預先註冊，**不得**當成判定讀」。下游的處置也一致：

- `CB_IS_THE_DEVICE_RESULT_2026-09-20`：把 `0.46` 判成 **two-regime fit artifact**（直接殘差在 m≤4 是
  **+0.16…+0.28 ms**、m≥6 轉**負**）⇒ 只判「**Partly — 6–13 ms/step total**」；
- `MF5_BARRIER_ATTRIBUTION_2026-09-20`：**F5 REFUTED** —— 每層 barrier **不是**可移除的 overhead；
- `NEXT_MILESTONES §…`：本 tree `src/` 裡**沒有 `barrier` 這個詞** ⇒ 它是**推算量不是插樁對象**。

⇒ **量具活著、可用**，但它產出的那個「~8%」是**一個已被降級的 post-hoc 截距**。要動工得先有
**可插樁的對象**，而那件事 repo 自己記著還沒有。

---

## 5. ★ 附帶一（我認為這一節最貴）：那個閘門本身 ——「±11.3% 噪聲底」

這一條不在你點的三個判詞裡，但**它是那三個判詞共同的前提**：`CANDIDATE_SPECS §508` 寫
「**M1（噪聲底 <5%）** 是剩下兩條（軸 R、2.3）**共同的判讀前提**。不先做，兩者都判不出來
（`±11.3% > 效應`）」。所以它的量具必須被稽核。

**它引的是**：`CANDIDATE_SPECS:24`「控制臂 `tg` 漂移 **±11.3%**（09-26 實測）」← `NEXT_MILESTONES §0`
「本次 **±6.5%~±11.3%**」。

**重算它自己的來源**（`Backup/phase_decomp/cbnmain_sweep3/{ctl64a,ctl64b}/bench.json`）：

```
ctl64a  tg = 10.1937   sd=1.266（12.4%）     14:37
ctl64b  tg = 11.1349   sd=0.564（ 5.1%）     14:46      Δ = +9.23%
```

- `CB_N_MAIN_BUCKETS §11.4` 對同一對臂寫 **+9.2%**（與重算相符）；**§12.2 對同一對臂寫 +15.5%**
  （`10.17 → 11.75`）——**同一份文件兩個不同的數**。
- **11.3% 在該文件裡只出現一次**：`:260` 的 `| ctl64b | 64 | 85/97 | 11.3% | OK |` ——
  那是 **`skip%`**（沒有時間戳的空 command buffer 比例），**不是漂移**。兩個量在同一頁相鄰兩張表。
- **那兩個控制臂本身在今天的契約下不可引用**：`thermal worst = HEAVY`，
  hist `{NOMINAL 38, MODERATE 26, HEAVY 111}`（**63% HEAVY**）與 `{55, 14, 90}`（**57%**），
  `attribution.verdict` 分別是 `both` 與 `thermal`。
- 重新表述噪聲底的那份 8 臂研究（`EIGHT_ARM_NOISE_FLOOR_2026-09-27`）**用 `llama_bench_matrix.py` 直跑**，
  它自己寫「**絕對值不可引用，且禁止跨時間比較**」；它給的「臂間極差 **11.9%**」**包含**那支已被
  `analyze.py` 事先判 **STALE**（`skip% 33%`）的 `off1`，**剔除後是 5.1%**；臂內 3-rep sd **3.2%**。
- **同一天稍晚、在合規入口上量到的**（`NEXT_ACTIONS §P2`）：`off` 跨狀態 `tg` **11.98 vs 12.02（~0.3%）**、
  Freebuff 四次 `tg` 散度 **1.3%**；更早見 `10.73–11.90（~11%）`。

⇒ **「任何小於 ~12% 的收益目前都判不出來」不是一個常數，而是一次 sweep 的產物，其輸入還被
契約本身判為不可引用。** 合規入口上的直接證據橫跨 **0.3%–11%**。而 repo 自己的規則其實已經寫對了方向
（「判讀門檻必須是**實測的控制臂漂移**，不是固定值」）——**問題出在下游把它引用成一個固定值。**

**這一節的處置建議**（不自行實作，供決策）：閘門改成**逐實驗、在合規入口上、用**
`attribution ∉ {both, swap, thermal}` 的臂**現量一對控制臂**，並把 `±11.3%` 標成「09-26 一次 sweep 的值」。

---

## 6. 附帶二：有一個量具的 selftest 在乾淨樹上**必 FAIL**

`python3 scripts/check/m123_oracle_gate.py --selftest` → **8/9**，唯一 FAIL：

```
[FAIL] the real tree records at least the gate script itself
```

它檢查的是 `tree_dirty()["dirty_src_hashes"]` 裡**有沒有 `scripts/check/m123_oracle_gate.py` 自己**
（`:842`）——也就是要求**這個腳本當下是 dirty 的**。⇒ 在已提交的樹上它**永遠不可能綠**。
這是「自我指涉的斷言」，不是缺陷；但它意味著**這支工具的綠/紅不能單獨當訊號**（8/9 才是健康狀態）。
**不是本輪改動造成的**（該檔未修改），列出來是因為它是一個「量具的量具」。

---

## 7. 引用紀律：這幾個數字現在該怎麼寫

| 現在的寫法 | 應該改成 |
|---|---|
| 「22.22（**同 cell 同 build**，ABBA）」 | 「22.22 由 **`llama-bench`／matrix 入口**產生（`CGC_SEG_BATCH` **09-25** 才進白名單，量測在 09-24）；同 cell 形狀下 B 臂族為 **20.06–22.22**；**在 `harness bench` 入口上尚未量過，且 09-25 之後才量得出來**」 |
| 「ρ **+14.0~14.5%**」 | 「白皮書**模型上界**（`cb=42.04`，該敏感性表自陳**沒有一個 h 有實測支撐**）；實測 `+4.7%` 是 **MTP-on 基準**下的 MAXQ 內部差，且該 A/B **繞過 launcher**（`CGC_RHO_PROBE/FILL` 09-26 才進白名單）」 |
| 「M-S2 判**不可達**」 | 「候選表 B／M-S2 **押後**；其 `19.0`／`17.36`／`22.9` 是**無量具的解析估計**，且 §8 自陳**推導未重建、不閉合（10.09 ms）**」（上一輪已更正） |
| 「每層 barrier **12.9 ms（~8%）**」 | 「**post-hoc 迴歸截距**（F1 §1.3 明文『不得當成判定讀』）；已降為 **6–13 ms**，且 F5 判**不可移除**、`src/` 無 `barrier` 可插樁」 |
| 「噪聲底 **±11.3%**」 | 「**09-26 一次 sweep** 的控制臂漂移（artifact 重算 **9.23%**；同文件另處寫 15.5%）；其兩臂皆 thermal HEAVY；**合規入口上的實測為 0.3%–11%** ⇒ 逐實驗現量，不得當常數」 |
| 「churn 的 42.3%」 | 「`ntok=4`（**MTP-on verify**）桶，且該批 run 的口徑是 **`prod25` ＋ 臂 `p25-s1-churn`**（`Backup/commit_msg_premise_b_measured_20260920.txt:18`）⇒ **兩層都不合規**；交付口徑（MTP off／`ntok=1` 主 context／`prod-new`）那一格**不存在**」 |

---

## 8. 本檔不主張

- **不主張任何判詞「錯」**。主張的是：三個判詞所依賴的數字，**兩個來自繞過交付入口的量測**，
  **一個是 post-hoc 擬合的截距**，而**共同的閘門本身是不可重現的常數**。這是「引用的來源」問題，
  不是「現象不存在」的問題。
- **不改任何判詞、不動 tier、不動 `src/`、不動別人的節點**（`io-constraint`、`m-decode25` 由 operator 決定）。
- **不動那支外來 `llama-bench`**：它寫 `Backup/phase_decomp/mtp_kbracket/off_a.json`，且用共用
  `--workdir /tmp/harness_bench`；並跑會覆蓋它的 stderr ⇒ 這是**硬性序列化**，不是禮貌問題。
- 不主張 `±11.3%` 是「查錯了」——它**可能**來自另一次 sweep。主張的是：**在它被引用的那條鏈上，
  沒有一個可重算的 11.3%，而鏈上唯一的 11.3% 是另一個量。**

## 9. 這份稽核的判準已經機械化（同日實作）

上面三問（**當時可不可以武裝**、**今天還活著嗎**、**入口與口徑對不對**）不再靠人讀文件讀出來：

```
python3 scripts/check/claim_instrument_check.py            # 全部；rc=1 = 至少一條 VOID
python3 scripts/check/claim_instrument_check.py 22.22      # 單一 claim
python3 scripts/check/claim_instrument_check.py --self-test # 14 例，ALL PASS
```

* `scripts/check/claim_instruments.yaml` —— 登記表（本檔 §0–§7 的 9 條 claim 全部入住）。
* `scripts/check/claim_instrument_check.py` —— 判準。

它對每一條宣稱回答四件事：`requires` 的每個開關①**首次**進白名單日 ≤ 量測日（`git log -S … --reverse`，
`-1` 是 last）；②現在還在白名單（單行 `SERVER_ENV+=(…)` **與** `for _v in … ; do` 兩種寫法都認）；
③字串還在**建置產物**裡（bytes 搜尋；**必須掃全部產物**——`CGC_SEG_BATCH` 在 `libggml-base`、
`CGC_CB_N_MAIN` 在 `libggml-metal`、`CGC_S1_OUT_CAP` 在 `libllama`，只看一個庫會把活的判成死的）；
④工具／符號／原始產物還在。

**本檔的結論表是這支工具的輸出，不是手抄**（`0 QUOTABLE / 1 WARN / 8 VOID`）——包含
`22.22` 與 `+4.7%` 的 `VOID-WRONG-ENTRY`、三個估計的 `VOID-NO-INSTRUMENT`、`42.3%`／`12.86` 的
`VOID-WRONG-CALIBER`、以及 `±11.3%` 的 `VOID-WRONG-ENTRY`。

⚠ 兩個刻意的設計，兩者都是本 session 的教訓：

1. **`VOID` ≠「現象不存在」**，是「這個數字不能這樣引用」。這支工具的 rc=1 是**現狀的正確描述**，
   不是回歸訊號（與 `gate_consistency` 的既有慣例一致）；接 CI 時要當報告用。
2. **`m123_oracle_gate --selftest` 的 8/9 是健康狀態**，不是故障（唯一 FAIL 是自我指涉那條，見 §6）。
   把它硬寫成 `rc==0` 就會把一支**活著的**工具報成死的——那正是這支工具要防的錯。
   登記表用 `selftest_ok_if: "8/9 passed"` 明寫這件事。

順帶得到一個「口徑豁免」的機制：只主張**數值身分**（不主張吞吐）的 claim 可以帶
`caliber_exempt: <理由>`，它會**降為 `WARN-DOWNGRADED` 並把理由印出來**——不是靜默放寬。

### 可複核命令

```bash
# 入口白名單的「首次」日期（--reverse 才是 first；-1 是 last，這是我第一版量錯的地方）
git log -S"CGC_SEG_BATCH" --reverse --format="%h %ad %s" --date=short -- scripts/run_server.sh | head -1

# 22.22 那支 ABBA 的入口（row 形狀 = llama_bench_matrix；extra_env 才是 B 臂）
python3 -c "import json;d=json.load(open('Backup/seg_batch_s1_pairs/res_B2.json'));print(d[0]['extra_env'],d[0]['rows'])"

# 噪聲底重算（我得到 +9.23%，文件兩處寫 +9.2%／+15.5%）
#   ctl64a 10.1937 → ctl64b 11.1349，兩份 bench.json 的 thermal.hist 皆以 HEAVY 為主
python3 -c "import json;a=json.load(open('Backup/phase_decomp/cbnmain_sweep3/ctl64a/bench.json'))"

# 三支量具是否活著
python3 scripts/check/cb_miss_regression.py --selftest
python3 scripts/check/m123_oracle_gate.py --selftest      # 8/9，唯一 FAIL 是自我指涉那一條
python3 scripts/check/premise_b_read.py --self-test       # 24/24（本輪修好兩條後）
```
