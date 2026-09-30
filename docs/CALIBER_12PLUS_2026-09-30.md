# 12+ 要进认证表：两条路的实际状态（2026-09-30 20:4x）

## 先纠正一个前提：那组 12+ 的**入口**也不同

`Backup/s3b_abba_2026-09-30/run.log` 显示它是：

```
arms=['rho-off','rho-on'] reps=2 profile=prod-new rounds=3 warmup=1 n_predict=120
... -> decode 12.97 t/s  hit 96.6%  miss 4613  md5 ['13448a8b']
```

带 **answer md5** ⇒ 这是 **llama-server 的 completion 回合**（round 级、`n_predict=120`），
而认证表 C3/C4 是 **llama-bench 的 cell row**（`p0/n64/d512`，逐 rep）。

⇒ 所以「同一个 prod-new profile」只是**模型/环境一致**，入口（server completion vs llama-bench）、
聚合单元（round vs rep）、形状（n=98/120 vs n=64）**全都不一样**。

## (a) 在权威 row 上重跑 —— 已发车，且有一条硬限制

- 做法：`harness bench --arm prod-new`（(default) cell 的 decode row，即 C3 那一行），
  要求 `attribution=none` ∧ thermal NOMINAL ∧ 逐 rep 离散 ≤ 1.10。
- ⚠ **硬限制（必须讲清楚）**：12+ 是 **ρ-on** 那一臂，而 `CGC_RHO_PROBE` 是 **R5 条目**
  （自带每层同步读回，`claim_instruments.yaml`：never quote throughput from it alone）；
  而 `CGC_RHO_FILL` 要靠 PROBE 的影子 logits 才能投递 ⇒ **机制与量具无法分离**。
  ⇒ 在 (a) 的干净口径下，**快的那一臂永远不可引用**；(a) 能认证的只有 ρ-off，也就是既有锚点的量级。
- 状态：20:40 发车；20:41 之前那趟被**压缩机起跑闸**拒（compressions 245 > 1.0 MiB/s，
  不加 `CGC_WINDOW_OVERRIDE`——那等于明知 DIRTY 还跑）；等闸转 QUIET 后重发。
  盒子被他线占用（`/tmp/harness_bench` 是共用工作目录），跑了 7 分钟尚未出结果。

## (b) 依 §2.5 宣告新口径 —— 不是"加一格"，是新增一条口径线

查证结论：`cell_contract` 描述的是 **llama-bench 的旗标**（batch/ubatch/prompt/gen/depths/reps/
warm_skip/load_mode/expert_cache_bytes…）。server completion 的 round 级形状**无法用这些旗标表达**
（`rounds`／`warmup`／`n_predict` 不是 bench 参数）。

⇒ (b) 实际需要的是三件事，**全部属于 operator 决策**：

1. **契约扩展**：让 `cell_contract` 能描述 server 路径的 round 口径（或另立一个 entry 层契约）。
2. **该口径自己的引用判据**：round 级的 attribution／thermal 怎么判、离散门槛多少、R5/R6 怎么套
   （现在 `quote_gate` 只认 bench 的 rep 向量）。
3. **明确这是新增口径，不是替换**：C3/C4 维持不动（否则就是把既有的一批数字 silent re-baseline——
   本 repo 三番两次拒绝过，例如「翻 MTP=0 默认值会静默 re-baseline 10.47／12.99 那一族」）。

⛔ 本档**未执行**任何宣告，只把"需要什么"写清楚，等 operator 点头。

## 一句话

**(a) 能刷新既有锚点，但拿不到 12+（快的那臂是 R5，机制与量具不可分）；
(b) 要拿 12+ 就得新增一条 server-round 口径线，那是 operator 的事，不是加一格 JSON 的事。**
在这两件事落地之前，12+ 只能是观测值。
