---
name: cgc-mtp-cost-curve
description: 在 flashkv-devserver（llama.cpp CGC fork，流式 MoE）上量測 speculative/MTP 的攤薄係數 m 與每步接受數 E，判斷「練 draft head 值不值得」。當使用者問「MTP 能不能加速／為什麼 MTP 虧／accept rate 要拉到多少才划算／cost(k)／攤薄」時使用。
agent_created: true
---

> **這是快照，不是權威副本。**
> 權威位置：`~/.workbuddy/skills/cgc-mtp-cost-curve/SKILL.md`（由 host 持續寫入）。
> 本檔於 2026-09-27 由 `agent_harness/scripts/import_harness_snapshot.py` 複製進 repo，唯一目的是讓 `agent_harness/`
> 底下的內容能被 `agent_harness/scripts/auto_git_push.ps1` 定時推送；原檔改了這裡**不會**自動跟上。
> 要改 skill 請改原檔，再重跑 `python3 agent_harness/scripts/import_harness_snapshot.py`。

# 量 MTP 的 cost(k) 曲線

## 什麼時候用

- 「MTP 能不能給 2-3×？」「為什麼開了 MTP 反而變慢？」
- 「accept rate 拉到 85% 值得嗎？」「該先練 head 還是先改 batch 路徑？」
- 任何要把 `E`（每步接受幾個 token）與「verify 批次成本」分開的場合。

## 核心模型（先背起來）

```
S(k) = E(k) / cost(k)          cost(k) = 1 + m·k        （單位＝一個 plain decode step）
E    = 1 + a · k_eff                                    a = 引擎報的 accept rate
硬上界：S <= (k+1) / (1 + m·k)      ⇒ m=1 時任何 accept 都救不了（S<=1）
漸近（k→∞）：  S → a / m     ★ a 與 m 是「比值同權」，不是主次
```

⛔ **2026-09-25 修正：上面那行 `E` 曾寫成 `(1 - a^(k+1))/(1 - a)`，那是錯的（會系統性低估）。**
`a` 有兩種語義，**引擎報的 accept rate 不是「每位置獨立接受機率」**：

| a 的語義 | E 的算式 | 代入 a=0.46541, k=3 |
|---|---|---|
| 引擎報的 accept rate（`accepted/drafted`）⇒ **用它** | `1 + a·k_eff`（**恆等式**） | **2.396** ＝實測 `mean_len 2.40` ✓ |
| 「每位置獨立接受機率 p」（理論模型） | `(1-p^(k+1))/(1-p)` | 1.783 ← **與實測差 26%** ✗ |

驗據：`docs/MTP_2X_BOUNDARY_2026-09-19.md` 明寫 `mean_len = 1 + k·a` 驗證過（`1+3·0.46541 = 2.396 ✓`）。
⇒ **不要把引擎的 accept rate 當 p 代入幾何公式。** 最直接的做法是別換算：`E = n_gen / rounds` 就是讀數。

**`m` 與 `a` 同權，不是 a 主導**：`k→∞` 的上界是 `a/m`（09-19 定稿：`0.4654/0.322 = 1.445`，
處方是「**兩邊各走一半**」）。所以結論的順序是：**先量 `m`，才知道該不該談 accept**。
2026-09-25 實測（`prod-new -p 0 -n 128 -d 512`，`k_eff=1`）彈性 `dlnS/dlna=+0.383` vs
`dlnS/dlnm=−0.465` ⇒ **在已量到的那一點上，m 的邊際影響略大於 a**（`D`，局部值，只適用 `k_eff=1`）。

⛔ **「`a/m = 0.714 < 1` ⇒ 沿 k 軸加到多大都虧」已作廢**（operator 支撐分級裁定，F17／F18）：
那是 `k→∞` 的極限，而線性 `cost = 1 + m·k` 已被 `m(k)` 散佈 1.40× 證偽 ⇒ 沒有支撐。
**「別加深」的正確寫法是用帶前提的門檻**（詳見 `docs/MTP_ON_OPTIMIZATION_PLAN_2026-09-25.md` §2.3–2.4）：
模型一（`mean_len = 1 + k·acc`）：`acc > c_tok/(C+c_tok) = 0.742` 才划算（今天 ≤0.62）；
模型二（chain，`p=0.5825`）：`m/c < 0.3754 / 0.1487 / 0.0694`（k=1→2／2→3／3→4），今天的係數是 0.7421。
⇒ 兩者同向（別加深），但它們是**條件命題不是實測**，裁定要靠多點 k sweep。

## 怎麼量（工具已存在，別重寫）

```sh
python3 scripts/check/spec_cost_curve.py --self-test          # 26 項，零 GPU，先跑
python3 scripts/check/spec_cost_curve.py --profile prefill250 \
    --ks 0,1,2,3,5,7 --rounds 3 --prompt 512 --gen 128 --depth 0 \
    --json Backup/phase_decomp/spec_cost_curve_<tag>.json
# 要判定「成本是不是 expert 流送造成的」，加第二軸：
#   --budgets 8,6,4   ⇒ per-layer slots 143/107/71，在固定 k 下移動 MiB/step
```

`E` 與 `cost` 都是**直接讀數，不是擬合**：

| 量 | 來源 |
|---|---|
| `E = n_gen / rounds` | `LLAMA_BENCH_SPEC_DBG=1` ⇒ 每輪一行 `SPECDBG round: n_done=.. draft=..`（`llama-bench.cpp:2631`） |
| 有效 draft 深度 `k_eff` | 同一行 `draft=` 的平均 —— **不是** `--spec-draft-n-max`（MTP 模組會自己截斷） |
| verify 專家並集 | 快取 teardown 無條件印 `verify: calls=.. union=..`（`llama-expert-cache.cpp:2529`） |
| **draft head 自己的 ms／輪** | `CGC_MTP_PERF=1` ⇒ stderr 一行 `CGC-MTP-PERF ... t_draft_ms=.. ms_per_round=.. emit_tok_per_round=..`（`common/speculative.cpp:3041`）。**已被 hoist**（`run_server.sh:2267`），所以 `--extra-env "CGC_MTP_PERF=1"` 就能拿到，不必改程式 |
| **每輪吐幾個 token（= 真正的 E）** | 同一行的 `emit_tok_per_round` ＝ `(acc_tokens + rounds)/rounds`，**它是 `E` 的另一個獨立來源**，不用再靠 `SPECDBG round` 去數 |

⚠️ **`CGC_MTP_PERF` 出來後，draft 前向的 ms／輪是直接讀數**，想隔離「成本有多少是 draft 前向」，
**先開這個，不要先去做 n-gram A/B**。但要引用「佔比」，分子分母必須**同一趟、同一 cell**：
⛔ 「draft 只佔一輪 7.5%（24.7 ms／317 ms）」**已作廢**（F21）：那兩個數字來自不同批
（24.7 取自斜率推估、317 取自另一趟），本輪其實沒有 `CGC-MTP-PERF` 輸出。
可引用的替代：`docs/MTP_K_SWEEP_2026-09-22.md` §3 用**同 harness 四點**擬出斜率
⇒ **draft head +8.35 ms/k（12%）vs target verify 路徑 +61.21 ms/k（88%）**（`dof=2`，輪內）。

warmup 的 generation 走非 spec 的 `test_gen(ctx,1)`（`llama-bench.cpp:2926`），所以輪數不會被 warmup 污染。
只有 `m` 是擬合的：最小二乘過原點，`cost-1 = m·k_eff`。

## 鐵律

1. **`--ks` 必須含 0**。一次 llama-bench 呼叫只能有一個 `--spec-draft-n-max` ⇒ k 曲線得分次呼叫；
   每個比值都用**同一輪自己的 k=0 基準**（工具會自動 ABBA 交錯、取輪內比值中位）。
   沒有 k=0 的曲線不能配對，工具會直接拒跑。
2. **絕對 t/s 通常不可引用**。18 支連續 bench 會把機器推到 HEAVY（基準臂自己掉 21%）。
   可引用的是輪內比值；報告要看 `clean` 欄與 `thermal(launch→worst)`。工具**還沒有 `--cooldown`**，
   若要乾淨絕對值，得自己分批跑並等 NOMINAL。
3. **`rc < 0`（訊號死亡）≠ 慢**。隔壁 session 的清場常以 `pkill -f 'llama-server|llama-bench'`
   形式出現（09-18 基準臂就這樣被 SIGKILL，rc=-9）。被殺的 run JSON 截斷、輪數偏短，
   混進中位會靜默偏掉曲線 ⇒ 工具會標記＋重試（預設 2 次）＋重試都死就排除。
   **assert／OOM 不重試** —— 那是「此 k 不可行」的結論。
4. **起跑前 preflight**（工具內建）：查 `llama-bench/llama-server/run_server.sh/http_duo.py/...`
   等競爭行程；有就 abort（`--force` 才硬上）。不要用 `pgrep llama` 自己判斷 —— 09-18 的競爭者
   是 `http_duo.py`，argv 裡沒有 `llama` 字串。
5. **union/call 是同一份資料裡最值錢的副產品**。它回答「verify 是不是真的把 draft token 的專家
   併成一個並集」：並集隨 k 明顯成長（實測 `9.36 + 3.12·k_eff`，單 token 一步＝8）＝ remap 有做；
   **恆為 8.00 ＝ 量到的是 exact-path fallback，那一輪整個作廢**（09-17 曾發生，09-18 已排除）。
   同時看 `verify-strict: zero_mapped_selected`，非 0 代表答案被靜默讀錯。
6. **★ 永遠用 `k_eff`，不要用 `--spec-draft-n-max` 去算 `E` 或 `m`。** 2026-09-25 實測：
   傳 `--spec-draft-n-max 3`，而 `SPECDBG round: ... draft=1` **逐輪皆是 1** ⇒ `k_eff=1`。
   用 3 去算會把 `E` 高估 43%、把 `m` 低估 3×，還會得出「加深 k 有用」的反向結論。
   ⇒ `a = E − 1`（k_eff=1 時），不是 `(E−1)/3`。
7. **⚠ 工具吃不到 `--warm-skip`**（`--extra` 只吃 env，`--warm-skip` 是 argv）⇒ 本工具跑出來的
   **絕對 t/s 缺交付口徑**（交付要求 `--warm-skip 64 -r 3`；無 warm-skip 時單臂 sd ±35%，
   有則 ±4.8%）。本工具的產物只引用**輪內比值**與整數計數器；要絕對值得手工跑或補這個開關。
8. **⛔ 公式推論只能建立在量測或數學上**（operator 2026-09-25 下令）。寫任何「⇒」之前先標等級：
   `M` 實測（能指出哪個產物檔哪一輪）／`D` 量綱恆等或定義式（不代入經驗假設）⇒ 可引用；
   `X` 外推到未量測的點／`A` 假設兩量獨立／`S` 推測因果／`U` 數字無出處 ⇒ **一律廢棄**。
   審計表與機檢：`scripts/check/formula_audit.py`（`--selftest` 13/13）。
9. **⛔ 別把單點反解的 `m` 當常數外推**。`k_eff` 恒為 1 時實測只有 `k∈{0,1}` 兩點 ⇒
   `m = cost−1` 是**單點反解不是擬合**。09-24 那份的 `m(k)` = 1.176／0.840／0.961／0.920（散佈 1.40×）
   ⇒ **`m` 不是常數**，`cost = 1 + m·k` 連他批自己的資料都不成立 ⇒ 任何「k=3 會怎樣／k→∞ 上界 a/m」
   的陳述都沒有支撐。要動 k 軸，**先掃多個 k**（warm-skip 口徑）。
10. **⛔ 跨報告搬數字先對量綱，再對 regime**。真實事故：`42.03 ms/verify-token`（`d(step)/dT` 的斜率）
   ÷ `48.2 ms/產出 token`（每產出 token 成本）——分子分母不是同一個量，除出來 0.872 看起來
   「與另一批的 0.868 只差 0.5%」，但同義口徑（`Δstep/step_off`）下是 **1.176 vs 0.868，差 35%**。
   另一個事故：把 09-24 的 draft 絕對時間除以本批 step 得到「draft 只佔 7.5%」——本輪根本沒有
   `CGC-MTP-PERF` 輸出，而那份自己也寫「絕對值不可與交付併排」。
11. **中位數是逐列獨立取的** ⇒ 「中位那一行」不是真實的一輪，用中位 `a`、`m` 反推出的 `S` **不是實測值**。
   要引用 `S` 就報**每一輪**的值（如 0.914／0.682／1.050）。

## 判讀

- `m >= 0.6`：幾乎沒攤薄 ⇒ 練 head 白花錢，去查 batch／並集路徑。
- `m <= 0.25`：攤薄良好 ⇒ accept 才是槓桿，值得練。
- 中間值：兩者都要。用 `S <= (k+1)/(1+mk)` 直接給天花板上界（實測 m=0.474 ⇒ a=0.92 只到 ~1.47×）。
  ⚠ **只有那個 k 真的量過才能用**（鐵律 9：`m` 不是常數，拿單點反解的 m 去算別的 k ＝ 外推，廢棄）。
- `E` 若隨 k 幾乎不動 ⇒ 加深度買不到 token，先查為什麼 `a` 這麼低（partial-restore 輪數也是線索）。

## 副產品：從 union 斜率反推 `p_route`，並估算「RSL 類」架構

`union(k_eff)` 的斜率就是「每個 draft token 平均帶來幾個**新**專家」（實測 3.61／滿 8）⇒
單專家邊際重疊率 `q = 1 − 斜率/8`（實測 0.55）。這是 `p_route` 的免訓練估計，不用去 dump 路由。

**但拿它估「draft 的 top-8 必須 ⊆ 已付費並集」（RSL-MTP）時會得到 0.70–0.97×、無一格 ≥1.0：**
`P(整個 top-8 都已在集合)` ≈ `q^8` = **0.008**（不是「略降」，是幾乎全砍）；放寬成「允許 ≤b 個新專家」
後 `Binomial(8, 1−q)` 顯示**接受率掉得永遠比成本快**（b=3：省 34% union／只留 47.5% 接受）。
而且「邊際成本 ∝ union」這個前提用兩點解 `(S_exp, c)` 會得到 **c<0**（四組配對全中）⇒ 否決。
⇒ **別再用 union 當槓桿去設計 draft 架構。** 詳見 `docs/RSL_MTP_GAIN_ESTIMATE_2026-09-18.md`。

## 每支 run 的 stderr 尾巴：成本主項就在那裡（先看這裡再決定怎麼優化）

`-p/-n/-d` 跑完後，teardown 會印出四行，**它們比 `m` 本身更能指出該修哪裡**：

```sh
grep -hE "read shape|decode/pool|layers_distinct_over_slots|miss attribution" \
  <workdir>/spec_cost_k*_r*_p*_n*_d*.stderr.log
```

1. `layers_distinct_over_slots=N` + `worst=layer L distinct=D slots=S` —— **N 層的 working set 超過
   該層 slot 配額**。實測 k=0..7：N = 0→4→15→17，`D/S` = 143/143→219/143。
   ⚠ **`k=0`（無 MTP）就已經 `143/143` 頂到配額 ⇒ 加任何 draft token 都立刻越界**，這解釋了為什麼
   `k: 0→1` 的跳變遠大於 union 計數的增長。
2. `hit%`：92.6%（k=0）→ ~50%（k≥1）。**崩點發生在 distinct 越界那一刻**，不是隨 k 漸進。
3. `read shape: bytes=`、除以步數 ⇒ `MiB/step`（實測 7.9 → 67）。回歸 `ms/step` 對 `MiB/step`：
   `ms = 89 + 3.35·MiB`（r²=0.70，等效 ~298 MiB/s）。⚠ **`MiB/step` 與 `k` 完全共線** ⇒ 這條回歸
   不能單獨定因；真正的機制證據是第 1 點那條**整數計數器**。
4. ⇒ ~~成本主項是 residency thrash~~ **此規則已被否決，見下方「第二軸」**。
   `layers_distinct_over_slots` 從 0 變非零只證明**有 thrash**，不證明**時間是它造成的**。

## 第二軸：pool budget（判定 thrash 到底值多少錢）

`--budgets 8,6,4` 會把 `-expert-cache <bytes>` 寫進 argv（per-layer slots 實測 143／107／71）。
**它是唯一能在 k 固定的情況下移動 `MiB/step` 的旋鈕**，用來打破上一條的共線。

09-18 結果（`prefill250 -p 512 -n 128 -d 0`，36 支）：

- 同 k 跨池的 **bytes→時間彈性只有 0.10–0.17**（k=1/3 甚至 −0.38）：
  k=0 時 bytes 4.68× 而時間只 1.25×；k=1 時 bytes 2.07× 而時間 **0.76×**。
- `k_eff` 單變數解釋 **84%** 的 `ms/step` 變異（59 ms/token）；bytes 只有 65%，
  且放進同一模型後 bytes 係數掉 3 倍（2.043 → 0.693 ms/MiB）⇒ bytes 大致是多餘的。
- `k_eff`（0.842）> `k`（配置的窗口，0.784）⇒ 成本跟著「真的 draft 出幾個 token」。
- draft 側只佔 union **3.0%** ⇒ 那 46–59 ms/token 不是 draft 自己去抓權重。
- ⇒ **`m` 的機械解釋是「每個 draft token 的固定代價」，修 residency 的上界只剩 ~15%**
  （超額 +269 ms 中 bytes 只解釋 ~40 ms；用 `8.5^0.145` 交叉驗算同一數字）。

⇒ **thrash／per-layer 配額／prefetch 開關降為次要。** 新的並列第一：

1. **`CGC_P_ROUTE=1`**（`llama-context.cpp:5929`、`llama-expert-cache.cpp:1799`）：探針**已在樹裡**，
   在 verify 步驟（`n_tokens>1`）直接印 `RSL p_route: ... i=1 X% i=2 ...` ＝
   `P(top8_{t+j} ⊆ top8_t)`，**不需要額外 forward**。跑一支 `--spec-draft-n-max 7` 就有答案，
   不必再用 union 斜率反推。
2. **n-gram draft**：同一個 k（verify 批次一樣是 k+1）但沒有 draft 前向。
   `m` 崩 ⇒ 成本是 draft 前向；`m` 不動 ⇒ 成本在 verify 的**每 token 路徑**（T=k+1 太小沒攤薄）。

**跑双轴前先修的三件事**（本輪全部踩到）：
① **run 順序與 budget 完全混淆** —— 每個 (round,k) 內永遠是 8→6→4，必須輪換／拉丁方；
② **漂移 −1.93 ms/run**（前 12 支 268 ms、後 12 支 188 ms）而熱態同時變壞 ⇒ 是 page-cache 預熱
   不是熱態，加 `idx` 協變量驗過係數不動（r² +0.001）；
③ 同格跨回合 max/min **1.03–1.74×** ⇒ 絕對 t/s 一律不可引用，只引用回合內比值與計數器。

**必查的環境事實**：`CGC_NO_PREFETCH=1` 是 `run_server.sh:2029` 的預設 ⇒ 上述全部是 prefetch 關閉下
量到的。要 A/B prefetch 就用 `CGC_PREFETCH_SRC=hist` 並 unset `CGC_NO_PREFETCH`（注意
`llama-expert-cache.cpp:1082` 記載的 deadlock class，且順序上排在 thrash 修好之後）。

## 別做

- **dynamic-k（自適應深度）**：用三輪資料算 oracle（每輪挑最好的 k）只得到 mean 1.035×／median
  1.068×，而這個上界還被噪音膨脹（同一 k=3 配置 r1=7.32 vs r2=11.49 t/s，1.57×）⇒ 真上界更低。
- **n-gram draft 的價值是診斷不是提速**：`--spec-type ngram-simple|map-k|map-k4v|mod|cache`
  （`common.h:177-181`）去掉 k 次 draft 前向 ⇒ 看 `m` 掉多少就知道 cost 裡有多少是 draft 前向。
  它省不掉 expert traffic：實測 draft 只佔 ~1%（`union=512` vs verify `union=47122` @ k=1）。

## ★ 單位陷阱：一輪 vs 一步（被問「verify 為何是好幾倍」時先拆這個）

**`cost(k)` 是「一輪」的時間，而 t/s 是「每個 token」的速度。兩者分母不同，直接比會得到 3–4 倍的假象。**

```
一輪 verify（k=3）= 317.4 ms   一步 plain decode = 92.2 ms   ⇒ 3.44×
但一輪吐 emit_tok_per_round = 3.197 個 token
⇒ 每 token 99.3 ms vs 92.2 ms                                ⇒ 1.08×
```

判讀時**永遠先算 `ms/token = step / emit_tok_per_round`**。實測（prod25／llama-bench／12 臂配對）：
k=0/1/2/3 的 ms/token 是 **92.2 / 96.9 / 92.5 / 99.3 —— 全場平坦**
⇒ **MTP 在這個形狀下每 token 一個都沒省**，一輪變大只是因為它一次做 4 個位置。

⚠️ 因此 **`m` 的邊際算法要小心**：`(step(k) − step(0))/k` 會把 draft head 與
`emit/round ≠ 1 + a·k` 混進同一個平均（實測誤差 0.24–1.33）。**用增量**：
`verify(k) = step(k) − ms_per_round(k)`，再逐 k 相減。

## 現成的 k 掃描 driver（prod25 口徑，已含 union／emit／draft ms）

```sh
python3 Backup/phase_decomp/k_sweep_ab.py --pairs 3 --reps 3     # k=0,1,2,3 旋轉配對
python3 Backup/phase_decomp/k_sweep_ab.py --pairs 1 --reps 3 --dry-run   # 先驗四臂只差 --spec-draft-n-max
```

它 monkeypatch `prod_matrix.CELLS['decode-mtp']`（= decode cell + `--spec-type draft-mtp`，
**`-b 512` 相同**），並從每支 run 的 `.stderr.log` 抓 union／`emit_tok_per_round`／`ms_per_round`。
k 由 `CGC_SERVER_MTP_N_MAX` 控制（`run_server.sh:436` ⇒ `--spec-draft-n-max`）。

09-21 結果：**`best_k = 0`**（t/s 10.845 / 10.320 / 10.809 / 10.072）⇒ **降 k 買不到速度，REFUTED**。
機制側 union 8.19→12.94→15.86→**18.09**（2.21×）確認無誤 —— 但把它修掉不值錢。

⚠️ **bench 側要先驗 `verify: calls=` 不是 0**：`CGC_VERIFY_DECODE` 只讓 fast path 對
`CGC_PHASE_VERIFY` 生效，而 phase 是 caller 設的。09-18 的 llama-bench 曾整輪
`verify: calls=0 draft: calls=N`（量到的是 exact-path fallback），現行 build 已修（實測 7553）。
每次換 build 都重驗一次。

## ★★ 比「ms/token」之前先對分母（09-21 我自己在這裡生出一個不存在的 2.4×）

三種分母混用，任何兩種拿來相比都是假的：

| 分母 | 例子 | 用途 |
|---|---|---|
| **位置數** `1+k` | 42 ms（= 167.81/4） | 問「一步做了幾個位置」 |
| **實際吐出 token** `emit_tok_per_round` | 99.3 ms（= 317.4/3.197） | 問「速度」 |
| **一輪** | 317.4 ms | 只問這一輪多大，不能跟「一步」比 |

我曾用「42（除以 4）vs 99.3（除以 3.197）」宣稱 verify 比 plain batch 貴 2.4×。
實測 plain M=4 總步時 **457 ms** vs verify 一輪 **317.4 ms** ⇒ **plain 反而貴 1.44×**。

**另一個同源陷阱：標定點要連它那一臂的形狀一起記。**
`167.81` 寫在記憶裡只記數字，但那一輪 `SERVER_MTP=1` ⇒ 它**就是** verify 自己。
⇒ 記憶規則：**數字旁邊必須寫 `MTP on/off` ＋ `--warm-skip` ＋ `--reps` ＋ cell**。

## ★ 真正的成本驅動項是 M（一步內的位置數），不是 MTP

`m_width_curve.py` 用 llama-bench／MTP off 掃 M=1…2048（cell 注入，繼承 profile 全部旗標）：

| M | 1 | 4 | 8 | 16 | 64 | 128 | 512 | 2048 |
|---|---|---|---|---|---|---|---|---|
| ms/位置 | 229 | 114 | 75.4 | 268 | 66.5 | **40.7** | **8.51** | 6.70 |
| step 總時 ms | 229 | 457 | 603 | **4288** | 4259 | 5211 | 4357 | 13722 |

- **M ≤ 8 = pool 路徑**：`step ≈ 176 + 53.4·M` ⇒ **`M→∞` 漸近只有 18.7 t/s**。
- **M ≥ 16 = slab 路徑**：**step 總時 ~4.28 s，與 M 無關** —— 引擎每次都填全部
  256 個專家（`the stream path sets ne[2]=n_expert`，11.9 GB ÷ 4.28 s ≈ **2.8 GB/s**）。
  ⇒ prefill 的高 t/s 是「一筆整包讀被 M 個 token 分攤」，**不是權重被攤薄**。
- **25 t/s = 40 ms/位置 ⇒ 需要 M ≈ 107**（實測 M=128 → 24.57 t/s）。
  **單流只能給 M = 1+k ≤ 5** ⇒ 差 20 倍 ⇒ **25 是「M 的問題」不是「單位置多快的問題」**。

```sh
python3 Backup/phase_decomp/m_width_curve.py --reps 3 \
  --extra-env "CGC_PREFILL_STREAM=1;CGC_GATHER_SLAB_CAP=256"
```

⚠️ **`CGC_PREFILL_STREAM=1` 必須配 `CGC_GATHER_SLAB_CAP=256`**，否則 M>8 直接
`GGML_ASSERT(n_tokens_all <= cparams.n_batch)`（stderr 自己會印該設哪個變數）。

## 已知未解（別當成結論引用）

服務路徑（prod25）量到 MTP **+31%**、m≈0.21；llama-bench（prefill250）量到 **0.64–0.74×**、m≈0.474。
混淆項（profile／儀器／draft 深度／batch）都沒驗證。
⚠️ ~~「prod25 在 llama-bench 上量不到」~~ **此說已過時**：09-21 用
`prod_matrix` + `CGC_PREFILL_STREAM=1;CGC_GATHER_SLAB_CAP=256;CGC_SERVER_EXPERT_CACHE_BYTES=8589934592`
在 prod25 上跑了 12 支 bench 成功（含 MTP on/off）。落差改用同儀器內的 k 掃描來解，不必動 `http_duo.py`。

**下一個該開的洞（09-21 新增）**：既有標定點說「同一條序列的 B=4 ubatch = 167.81 ms ⇒
**42 ms/token**」，而 MTP verify 4 個位置是 **99.3 ms/token**。同樣 4 個位置、同一個模型，
一個攤得動一個攤不動，差 **2.4×**，目前沒有儀器指到它。
最便宜的下一步：在現行 build 重測一次 plain B=4 ubatch —— 仍是 42 ⇒ 那 2.4× 是 verify 路徑
本身的缺陷（真槓桿）；也退化到 ~99 ⇒ 沒有缺口，此軸結案。

## 落盤

讀數要內嵌 `docs/*.md`：原始 json 放 `Backup/`，而 `Backup/` 被 `.gitignore:396` 排除 ⇒ 不能引用。
