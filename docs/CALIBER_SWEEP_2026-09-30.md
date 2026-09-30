# 口径合规盘点（2026-09-30 21:2x）—— 把 operator 新规落到既有产物上

规则（operator 2026-09-30）：**量测必须统一口径，非统口径不得参与量测／认证。**
统一口径＝① 入口 harness bench ② 格子属测试卡 §2.5 ③ 逐 rep 聚合。

命令（可重复执行）：

```
python3 scripts/check/caliber_gate.py --sweep 'Backup/*2026-09-30*/*.json'
```

## 结果：61 份产物 ⇒ 48 合规／2 不合规／11 未知

| 分类 | 数量 | 处置 |
|---|---|---|
| **UNIFIED** | 48 | 可参与量测（但**还要过 `quote_gate`** 才算可引用） |
| **NON_UNIFIED** | 2 | ⛔ **不得参与量测** |
| **UNKNOWN** | 11 | ⛔ fail-closed：扫不出格子与 rep 向量 ⇒ 同样不得参与（不是"待定"，是"不合规"） |

### 不合规（NON_UNIFIED）逐项

- `s2c_2026-09-30/delivery.json` —— server completion 的 round 级读数（非 llama-bench 的 cell row）。
- `s3b_abba_2026-09-30/aa_probe_n4.json` —— 格子名不是 §2.5 的宣告值。

### 未知（UNKNOWN）主要是两类

- `s3b_abba_2026-09-30/*`（rho_AB／rho_BA／verdict／aa_*，共 8 份）—— 那组 **12+ 的来源**：
  没有格子、没有 bench 的 rep 向量 ⇒ **依新规不得参与量测**。
- `l251_loadmode_2026-09-30/*.json`、`mmid_delivery_2026-09-30/*` —— 同样看不出格子与 rep 向量。

## 顺带收尾：(a) 那趟权威 row 重跑的结果

`Backup/cert_row_2026-09-30/default_row.json`：

- **口径闸：UNIFIED**（cell=(default)、逐 rep）⇒ 符合新规。
- **引用闸：0/2 可引用** —— decode row 逐 rep [9.75, 10.93, 10.49]、`max/min=1.122 > 1.10`（差 0.02）、
  `attribution=both`；prefill row 也只有 149–155（该格平时 ~290）。
- 原因：**他线当时正在同一台盒子上跑 llama-server（port 8080）** ⇒ 我这侧的量测被拖成 `both`。

⇒ 结论：**盒子被占用期间不要再发量测车**（一定是 DIRTY）；要与他线错开时间。

## 两个闸门自己的 bug（已修进 selftest）

1. 格子清单读不到时必须立刻 `UNKNOWN`（原先会落到后面的检查而误判成 NON_UNIFIED 的"确定"结论）；
2. `cell` 常是**巢状栏位** ⇒ 只看最上层会误判成 `(default)`；改成全文取值并**验证形状**
   （第一版把文档里的模板 `"cell": "<name>",` 当成格子名，读出一个叫 `<name>",` 的格子）。
