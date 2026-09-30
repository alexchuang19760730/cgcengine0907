# 明天开工清单（2026-09-30 22:2x 立）—— 把剩 6 格变 4，再啃 2

## ⓪ 开工前先问三个闸（不要凭感觉发车）

```
python3 scripts/check/dispatch_ready.py --check            # 发车间（四条）
python3 scripts/check/s2c_ready.py --check                 # S2-c 就绪闸
python3 scripts/check/s1_watch.py --check                  # S1 进展指纹
```

发车间现在是**四条**（今晚多加了 swap 回收）：`procs` 无人占用、`compressor` QUIET、
`thermal` NOMINAL、**`reclaim` RECLAIMED（swap used ≤2048 MiB 且 free ≥4096 MiB）**。
刚加完实测：`DIRTY_BOX swap used 4958 MiB；free 846 MiB` ⇒ **BLOCKED**——
这就是今晚连发三趟全是 DIRTY/UNSTABLE 的真正原因：**压缩机安静 ≠ 盒子已回收**。

## ① 看板一释放就贴这两行 ⇒ 6 → 4

看板 YAML（`scripts/check/decode_board_2026-09-29.yaml`）目前在他线工作树 ⇒ 等它 clean 再改，
改前照例先 `git status` 确认（检查与动作同一支）。

### L20-10：判别完成（两个候选都**否证**）

```yaml
        runnable: "⛔ 不用再跑 — 判别已完成：B1（warm_skip 256）未达、B3（batch 512）**否证**（decode Δ −1.7% 在噪声内）⇒ 该跨格差<b>没有单一机制主因</b>。"
        badge: {text: "結案（排除）·兩候選否證", tone: dead}
        state: "結案（排除·否證）——《b>兩個候選都不成立</b>：① <code>delivery-ws256</code>（warm_skip 256）逐 rep 仍單調升（8.84→10.86→10.99）⇒ 量測起點不是主因；② <code>default-b512</code>（只改 batch）decode 平台 <b>10.95</b> 對照 b5632 的 <b>11.14</b>（Δ <b>−1.7%</b>，噪聲內）⇒ batch 不是主因。<br>★ 新發現：<b>batch 主要打 prefill（252.6 → 106.9，−58%），幾乎不打 decode</b> ⇒ 解釋了交付格 pp 低。<br>⇒ 11.703 與 8.26 那道跨格差<b>沒有單一機制主因</b>，傾向 §56 不可比／盒子狀態。"
        evidence: {profile: prod-new, entry: "harness bench", value: "A=(default) b5632 decode 平台 <b>11.14</b>／prefill 252.6；B=default-b512 decode <b>10.95</b>／prefill <b>106.9</b>；B1=delivery-ws256 [8.84, 10.86, 10.99]。三趟同窗、口徑閘 UNIFIED；attribution 皆非 none ⇒ <b>時間端點僅上界</b>", metric: "platform_tps", meets: false, cert_by: "前置否證（兩候選都否證，非達標）", source: "docs/S2_S3_2026-09-30.md；Backup/l2010_clean_2026-09-30/{A_b5632,B_b512}.json；Backup/l2010_b1_2026-09-30/B1_ws256.json"}
```

### L25-1：SECONDARY **不可测**（非否证）

（全文见 `docs/OPERATOR_RULING_L251_2026-09-30.md`，此处为待贴文本）

```yaml
        runnable: "⛔ 不用再跑 — operator 2026-09-30 裁定<b>不換盒子、不降模型</b> ⇒ B 臂（mmap）在 pool 8 GiB 與 6 GiB <b>皆</b> Metal OOM（rc=−6），而再降 pool 會與 A 臂不同形（pool 本身改 miss ⇒ 混淆）⇒ <b>同形不可測</b>，出路已關閉。"
        badge: {text: "PRIMARY 否證；SECONDARY 不可測（出路關閉）", tone: dead}
        state: "結案（排除·不可測）（operator 2026-09-30：不換盒子／不降模型）——<b>PRIMARY 已否證</b>（ngram 的 E <b>0.984</b>）；SECONDARY＝<code>load_mode=mmap</code>：A 臂跑通（miss/step 中位 <b>6.0</b>、有 miss 層數中位 <b>6.0/39</b>、正規化 <b>1.92%</b>），B 臂在 pool <b>8 GiB 與 6 GiB 皆</b> <code>Insufficient Memory</code>、rc=<b>−6</b> ⇒ <b>同形不可測</b>。<br>⛔ <b>這不是否證</b> ⇒ 本格自己的否證句「SECONDARY 否證 ⇒ 25 定案判死」<b>不觸發</b>；25 的定案現在<b>只掛在 S1</b>。"
```

## ② S2-c：他线一放下 src，一趟就落

```
python3 scripts/check/s2c_ready.py --check            # tree CLEAN ＋ patch APPLIES ＋ window FREE ⇒ READY
git apply patches/s2c_rho_capture_out_of_hook.patch
# 建置前：会 abort 的闸门（listener ＋ pgrep），检查与动作同一支
# 建置后：先确认 stderr 出现 CGC-RHO-CAP（读回非空），再跑见证
python3 scripts/check/m123_oracle_gate.py --profile prefill250 \
  --ref Backup/knifeedge_matrix/ref_iq3_pool8gb_M2_6144_bitident_v9_20260930.jsonl \
  --env CGC_SEG_BATCH=1 --env CGC_MISS_MASK=1 --env CGC_RB_FEED=1 \
  --env CGC_RHO_PROBE=1 --env CGC_RHO_FILL=1 --env CGC_RHO_FEED=1 \
  --tag s2c-witness-2026-10-01
```
判別句：**M1/M2 ≥ 8/9 ⇒ R6 解除 ⇒ 20+ 臂可引用**；≤ 2/9 ⇒ 分歧在单段提交图本身（`CGC_B_SCHEME` garbage）⇒ 升级为真引擎手术。

## ③ 两线分时段独占窗口（待 operator 协调，非我能单方决定）

- 现状：共用 `/tmp/harness_bench` 与同一台盒子 ⇒ 并发量测互相拖成 `both`（今晚 A 趟 prefill 只剩 149–155，该格平时 ~290）。
- 建议：**一条线量测时另一条只做静态/补丁**；或各配独立 workdir 并严格错开时段。
- 我这侧已加：发车间（四条，含 swap 回收）＋ 每日一次标记。剩下的 swap 存量要等盒子自己回收（必要时重启／清内存）⇒ 那一步不在我的权限内。

## ④ 剩下真硬的两格 ＋ 一个存量缺口

| 格 | 卡点 | 下一步 |
|---|---|---|
| **S1（25 唯一活路）** | 他线引擎工作（共用 KV 的 verify 布局是设计取舍） | 第一趟**先带足迹闸**（MTP-on ≥7703 MB、`budget_gate` 现判 OVERBUDGET）；跑不完 ⇒ 25 是**盒子判死**（非机制判死）⇒ 回 L25-4 口径决策 |
| **S2-c（20+ 钥匙）** | 等 `llama-context.cpp`／`run_server.sh` 释放 | 见 ② |
| **L25-2** | 缺「上界以外的新机制」指名 | 存量缺口：没人指名就不存在可跑的东西 |
