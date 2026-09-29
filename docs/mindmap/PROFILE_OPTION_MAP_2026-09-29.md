# 250 / 25 攻關：52 條 × profile option 對照表（2026-09-29）

> 規則：`docs/MEASUREMENT_CONTRACT_2026-09-25.md` §5（四級）＋ 臂語法 `<profile>:<option>=<值>`（charter 規則「臂：預設一律基於 prod-new」）
> 產生：`python3 scripts/check/mindmap_profile_audit.py --apply --report`；資料 `docs/mindmap/mindmap.json` 的 `arm_binding` 欄位。

## 統計

| 階段 | 條數 | 有實跑 log | 有報告 | 綁定來自實跑／charter | 缺口 |
|---|---|---|---|---|---|
| **生產交付** | 15 | 9 | 15 | 0 | 0 |
| **實驗階段** | 19 | 14 | 15 | 9 | 0 |
| **已結案（廢棄／不適用）** | 18 | 10 | 18 | 0 | 0 |
| **合計** | **52** | 33 | 48 | 9 | **0** |

## 逐條對照

| # | 條目 | 階段 | profile | option（被測） | 來源 | 測試 log／報告 | 白皮書 |
|---|---|---|---|---|---|---|---|
| | **生產交付（15）** | | | | | | |
| 1 | [prefill ≥ 250（交付 cell）](briefs/m-prefill250.md) | 2 | `prefill250` | `CGC_PREFILL_PROTECT=1` | ③ 證據文件掃描 | [log](../../Backup/llama_bench/prefill_certifiability_20260916.json)　[log](../../Backup/llama_bench/prefill_certifiability_warm_20260916.json)　[報告](../PREFILL250_CONDITIONAL_DELIVERY_20260916.md) | [md](briefs/m-prefill250.md)　[html](briefs/m-prefill250.html) |
| 2 | [① 攻關成功（pp≥250 ∧ tg>12.57）](briefs/m-total.md) | 1 | `prod-new` | `CGC_LAYER_AHEAD_PREFETCH=1` | ③ 證據文件掃描 | [log](../../Backup/nofill_prod/nf2_fill.json)　[log](../../Backup/nofill_prod/nf_fill.json)　[報告](../MILESTONE_MAP_RECHECK_2026-09-25.md) | [md](briefs/m-total.md)　[html](briefs/m-total.html) |
| 3 | [池大小掃描（8→4→2 GiB）](briefs/io-poolsize.md) | 3b | `prod25` | `CGC_SERVER_EXPERT_CACHE_BYTES=0` | ③ 證據文件掃描 | [log](../../Backup/cache_size_ab/ds_346A.json)　[報告](../IO_PATH_AB_2026-09-21.md) | [md](briefs/io-poolsize.md)　[html](briefs/io-poolsize.html) |
| 4 | [S1 探針臂：slot table 放 GPU（數值身分）](briefs/s1-probe.md) | 3b | `prod-new` | `CGC_MM_BITIDENT=1` | ③ 證據文件掃描 | [報告](../S1_LINE_VERDICT_2026-09-25.md) | [md](briefs/s1-probe.md)　[html](briefs/s1-probe.html) |
| 5 | [3a：未填充 expert 貢獻歸零（MISS_MASK / ZERO_MISS）](briefs/miss-3a.md) | 3b | `prod-new` | `CGC_SERVER_STRICT_BUDGET=1` | ③ 證據文件掃描 | [log](../../Backup/miss_axis_mtpoff_ws64_r3/miss_axis_res.json)　[報告](../GAP_VS_MISS_2026-09-20.md) | [md](briefs/miss-3a.md)　[html](briefs/miss-3a.html) |
| 6 | [MTP 儀器化（接進 llama-bench）](briefs/mtp-instrument.md) | 3b | `prod-new` | `LLAMA_BENCH_SPEC_NOTRIM=1` | ③ 證據文件掃描 | [報告](../MTP_HEAD_PROVENANCE_GATE_2026-09-14.md) | [md](briefs/mtp-instrument.md)　[html](briefs/mtp-instrument.html) |
| 7 | [MTP 口徑與混淆定位（pool／跨啟動／順序）](briefs/mtp-caliper.md) | 3b | `prod25` | `CGC_SERVER_MTP=0` | ③ 證據文件掃描 | [log](../../Backup/phase_decomp/aligned_nail_vs_ornith_fwd.json)　[log](../../Backup/m123_oracle_gate/summary_en-orn-ab.json)　[報告](../MTP3_25TPS_ARITHMETIC_2026-09-18.md) | [md](briefs/mtp-caliper.md)　[html](briefs/mtp-caliper.html) |
| 8 | [MTP 到 2× 的邊界（攤薄算術）](briefs/mtp-2x.md) | 3b | `prod-new` | `CGC_P_ROUTE=1` | ③ 證據文件掃描 | [報告](../MTP_2X_BOUNDARY_2026-09-19.md) | [md](briefs/mtp-2x.md)　[html](briefs/mtp-2x.html) |
| 9 | [cb 口徑定讞（42 vs 74）](briefs/cache-cb.md) | 3b | `prod25` | `（無自己的 option）` | ③ 證據文件掃描 | [報告](../CB_DELIVERY_SETTLED_2026-09-23.md) | [md](briefs/cache-cb.md)　[html](briefs/cache-cb.html) |
| 10 | [K3 邊際單價（dispatch 成本）](briefs/k3-price.md) | 3b | `prod25` | `（無自己的 option）` | ③ 證據文件掃描 | [log](../../Backup/m123_oracle_gate/summary_en-k3pair-cert.json)　[報告](../K3_CEILING_REAUDIT_2026-09-21.md) | [md](briefs/k3-price.md)　[html](briefs/k3-price.html) |
| 11 | [shape／knob 世界模型（14–15 個旋鈕的邊界）](briefs/shape-knobs.md) | 3b | `prefill250` | `（無自己的 option）` | ③ 證據文件掃描 | [log](../../Backup/phase_decomp/L3/autotune_nsg.json)　[log](../../Backup/m123_oracle_gate/summary_p2_rebased.json)　[報告](../BEST_SHAPE_IQ3XXS_M4_2026-09-22.md) | [md](briefs/shape-knobs.md)　[html](briefs/shape-knobs.html) |
| 12 | [swap 結構修復（L0–L4 + P0/P1/P2）](briefs/sys-swap.md) | 3b | `prod-new` | `CGC_SERVER_MTP=1` | ③ 證據文件掃描 | [報告](../SWAP_STRUCTURAL_FIX_2026-09-24.md) | [md](briefs/sys-swap.md)　[html](briefs/sys-swap.html) |
| 13 | [server 窗口／box 准入（單一來源閘門）](briefs/sys-window.md) | 3b | `prod-new` | `（無自己的 option）` | ③ 證據文件掃描 | [log](../../Backup/phase_decomp/server_window_audit.json)　[報告](../BOX_ADMISSION_SINGLE_SOURCE_2026-09-20.md) | [md](briefs/sys-window.md)　[html](briefs/sys-window.html) |
| 14 | [expert cache 血統設計（09-05~09-09 期）](briefs/na-design.md) | 3b | `prod-new` | `CGC_WARM_NPAST=256` | ③ 證據文件掃描 | [報告](../archive/pre-consistency-metrics-2026-09-11/CGC_EXPERT_CACHE_INVARIANTS_v1.md) | [md](briefs/na-design.md)　[html](briefs/na-design.html) |
| 15 | [成績排行榜（最高配置＋檢驗檔）](briefs/score-leaderboard.md) | 2 | `prod25` | `CGC_PREFILL_STREAM=1` | ③ 證據文件掃描 | [log](../../Backup/k3_pair_cert_2026-09-28/logs/r4_b_k2/launch01.json)　[log](../../Backup/leaderboard/20260928_233550_launch01.json)　[報告](../K3_PAIR_CERT_VERDICT_2026-09-28.md) | [md](briefs/score-leaderboard.md)　[html](briefs/score-leaderboard.html) |
| | **實驗階段（19）** | | | | | | |
| 1 | [CGC_EB_NOFILL 診斷臂（fill 成本）](briefs/io-nofill.md) | 3a | `prod-new` | `CGC_PREFILL_STREAM=1` | ③ 證據文件掃描 | [log](../../Backup/nofill_prod/nf_fill.json)　[報告](../IO_AXIS_VERDICT_2026-09-25.md) | [md](briefs/io-nofill.md)　[html](briefs/io-nofill.html) |
| 2 | [單段提交（41 段 → 1 段）](briefs/s1-segbatch.md) | 3a | `prod-new` | `CGC_S1_TABLE_CHURN=1` | ③ 證據文件掃描 | [log](../../Backup/m123_oracle_gate/summary_s1-nodbg.json)　[log](../../Backup/m123_oracle_gate/summary_s1-dbg-fixed2.json)　[報告](../S1_DIAGNOSTIC_ABORT_ROOT_CAUSE_2026-09-20.md) | [md](briefs/s1-segbatch.md)　[html](briefs/s1-segbatch.html) |
| 3 | [異步 gather 流水線（單段＋miss 後台補＋局部重算）](briefs/s1-asyncgather.md) | 3a | `prod-new` | `（無自己的 option）` | ③ 證據文件掃描 | [log](../../Backup/nofill_prod/nf_fill.json)　[log](../../Backup/eseries/E4/summary.json)　[報告](../S1_ASYNC_GATHER_PIPELINE_2026-09-25.md) | [md](briefs/s1-asyncgather.md)　[html](briefs/s1-asyncgather.html) |
| 4 | [MTP k-sweep（verify batch T 成本曲線）](briefs/mtp-ksweep.md) | 3a | `prod25` | `CGC_NO_PREFETCH=1` | ③ 證據文件掃描 | [log](../../Backup/phase_decomp/pool_budget_runs_20260918_2251.json)　[log](../../Backup/s1_ksweep/20260924_225548/s1_ksweep.json)　[報告](../BIGMOMO_TRANSFER_AND_MTP_PARAM_PARITY_2026-09-19.md) | [md](briefs/mtp-ksweep.md)　[html](briefs/mtp-ksweep.html) |
| 5 | [ρ 路線（按層批次化 prefetch）](briefs/cache-rho.md) | 3a | `prod-new` | `CGC_SERVER_MTP=1` | ③ 證據文件掃描 | [log](../../Backup/cgc_logs/f1_cb_miss_regression_report.json)　[報告](../F1_CB_MISS_REGRESSION_RESULT_2026-09-20.md) | [md](briefs/cache-rho.md)　[html](briefs/cache-rho.html) |
| 6 | [prebind／方案 A（預指派 slot）](briefs/cache-prebind.md) | 3a | `prod-new` | `（無自己的 option）` | ③ 證據文件掃描 | [報告](../H_MEASURED_2026-09-24.md) | [md](briefs/cache-prebind.md)　[html](briefs/cache-prebind.html) |
| 7 | [k=3 的 1.43× 飄移定位 ＋ 配對認證](briefs/k3-swing.md) | 3a | `prod25` | `CGC_SERVER_MTP_N_MAX=2` | ③ 證據文件掃描 | [log](../../Backup/prod_profile/prod_profile_20260920_1230.json)　[報告](../K3_PAIR_CERT_2026-09-23.md) | [md](briefs/k3-swing.md)　[html](briefs/k3-swing.html) |
| 8 | [device span 歸因（最大一塊時間）](briefs/c-device-span.md) | 3a | `prod25` | `CGC_CB_N_MAIN=1` | ③ 證據文件掃描 | [log](../../Backup/phase_decomp/L3/shape_probe/mmid_gather.json)　[報告](../CB_IS_THE_DEVICE_RESULT_2026-09-20.md) | [md](briefs/c-device-span.md)　[html](briefs/c-device-span.html) |
| 9 | [頻寬屋頂／dense GEMV 上界](briefs/c-bandwidth.md) | 3a | `prod-new` | `CGC_MMV_NSG=8` | ③ 證據文件掃描 | [log](../../Backup/phase_decomp/L3/shape_probe/nsg_dense_lmhead.json)　[報告](../DENSE_GEMV_INSTRUMENT_2026-09-22.md) | [md](briefs/c-bandwidth.md)　[html](briefs/c-bandwidth.html) |
| 10 | [G1：可達上界 / G1-G7 sweep](briefs/g1.md) | 3a | `prod-new` | `CGC_SUBMIT_AHEAD=1` | ③ 證據文件掃描 | [log](../../Backup/phase_decomp/joint_capture_20260920_2144.json)　[log](../../Backup/phase_decomp/L3/shape_probe/mmid_nsg_sweep_rotated.json)　[報告](../G1_ACHIEVABLE_CEILING_2026-09-20.md) | [md](briefs/g1.md)　[html](briefs/g1.html) |
| 11 | [G0–G7 序列化／調度消減（41 段→1 段、縮 union、藏 …](briefs/exp-s-retro.md) | 3a | `prod-new` | `CGC_HOOK_SPLIT=1；CGC_CB_N_MAIN=32` | ① 實跑 arm | [log](../../Backup/phase_decomp/cbnmain_pair/on1/bench.json)　[log](../../Backup/phase_decomp/cbnmain_pair/off2/bench.json) | [md](briefs/exp-s-retro.md)　[html](briefs/exp-s-retro.html) |
| 12 | [C：有效帶寬 13.65 → ≥29 GB/s（16／25 t/s）](briefs/exp-c-eff-bandwidth.md) | 3a | `prod25` | `（無自己的 option）` | ② charter arms | [報告](../DECODE25_CEILING_2026-09-20.md) | [md](briefs/exp-c-eff-bandwidth.md)　[html](briefs/exp-c-eff-bandwidth.html) |
| 13 | [C：讀取發行開銷（重驗 io-shape）](briefs/exp-c-read-issue.md) | 3a | `prod25` | `（無自己的 option）` | ② charter arms | [報告](../S2_OVERLAP_EXPERIMENT_2026-09-28.md) | [md](briefs/exp-c-read-issue.md)　[html](briefs/exp-c-read-issue.html) |
| 14 | [S2：段邊界免等（天花板 18.7 t/s）](briefs/exp-s2-overlap.md) | 3a | `prod25` | `CGC_SLOT_TABLE_GPU=1` | ② charter arms | [log](../../Backup/cgc_logs/f1_cb_miss_regression_report.json)　[log](../../Backup/phase_decomp/s2a_policy_census.json)　[報告](../S2_PROBE2_AND_CEILING_2026-09-20.md) | [md](briefs/exp-s2-overlap.md)　[html](briefs/exp-s2-overlap.html) |
| 15 | [M：攤薄係數 m 0.474 → ≤0.073](briefs/exp-m-draft-cost.md) | 3a | `prod25` | `（無自己的 option）` | ② charter arms | [報告](../DECODE25_CEILING_2026-09-20.md) | [md](briefs/exp-m-draft-cost.md)　[html](briefs/exp-m-draft-cost.html) |
| 16 | [口徑＋k=3 飄移認證（16.4% 噪聲底）](briefs/exp-caliber-calibration.md) | 3a | `prod25` | `（無自己的 option）` | ② charter arms | [報告](../DECODE25_CEILING_2026-09-20.md) | [md](briefs/exp-caliber-calibration.md)　[html](briefs/exp-caliber-calibration.html) |
| 17 | [在**今天的交付口徑**上（`prod-new` profile、`…](briefs/exp-churn-delivery-630.md) | 3a | `prod-new` | `CGC_S1_TABLE_CHURN=1；CGC_SLOT_TABLE_GPU=1` | ① 實跑 arm | [log](../../Backup/churn_delivery_630_2026-09-28/churn.json)　[log](../../Backup/exp_runs/exp-churn-delivery-630_20260928_231234.json) | [md](briefs/exp-churn-delivery-630.md)　[html](briefs/exp-churn-delivery-630.html) |
| 18 | [在**認可入口**（`harness bench`、`prod-ne…](briefs/exp-prodnew-decode20-g4miss.md) | 3a | `prod-new` | `CGC_SEG_BATCH=1；CGC_B_SCHEME=1；CGC_SLOT_TABLE_GPU=1；CGC_MISS_MASK=1；CGC_MISS_MASK_COST=1` | ① 實跑 arm | [log](../../Backup/g4miss_2026-09-28/g4miss_delivery.json)　[log](../../Backup/g4miss_2026-09-28/g4miss2_default.json) | [md](briefs/exp-prodnew-decode20-g4miss.md)　[html](briefs/exp-prodnew-decode20-g4miss.html) |
| 19 | [在單次提交臂上，**把每一步的 union 不經 hook 交給預取…](briefs/exp-singlesubmit-fillahead.md) | 3a | `prod-new` | `CGC_SEG_BATCH=1；CGC_B_SCHEME=1；CGC_SLOT_TABLE_GPU=1；CGC_MISS_MASK=1；CGC_MISS_MASK_COST=1；CGC_SPAC_DBG=1` | ① 實跑 arm | [log](../../Backup/fillahead_2026-09-28/fed_default.json)　[log](../../Backup/exp_runs/exp-singlesubmit-fillahead_20260929_012703.json) | [md](briefs/exp-singlesubmit-fillahead.md)　[html](briefs/exp-singlesubmit-fillahead.html) |
| | **已結案（廢棄／不適用）（18）** | | | | | | |
| 1 | [decode ≥ 25（M-25）](briefs/m-decode25.md) | 4 | `prefill250` | `CGC_SERVER_EXPERT_CACHE_BYTES=8589934592` | ③ 證據文件掃描 | [log](../../Backup/cgc_logs/joint_step_accept_g6.json)　[log](../../Backup/phase_decomp/io_granularity_20260921_020826.json)　[報告](../CEILING_STACK_2026-09-21.md) | [md](briefs/m-decode25.md)　[html](briefs/m-decode25.html) |
| 2 | [IO 請求形狀（合併 pread）](briefs/io-shape.md) | 4 | `prod-new` | `CGC_SPAC=0` | ③ 證據文件掃描 | [log](../../Backup/phase_decomp/ab_nospac_prodnew.json)　[log](../../Backup/nofill_prod/nf_fill.json)　[報告](../EXPERT_CHANNEL_SHAPE_2026-09-23.md) | [md](briefs/io-shape.md)　[html](briefs/io-shape.html) |
| 3 | [M-W：WORKERS 8→2](briefs/io-mw.md) | 4 | `prod-new` | `CGC_SERVER_WORKERS=8` | ③ 證據文件掃描 | [log](../../Backup/mw_ab/mw_ctrl.json)　[log](../../Backup/mw_ab/mw_w2.json)　[報告](../M_W_DELIVERY_VERDICT_2026-09-21.md) | [md](briefs/io-mw.md)　[html](briefs/io-mw.html) |
| 4 | [CGC_LAYER_AHEAD_PREFETCH（提前一層）](briefs/io-layer-ahead.md) | 4 | `prod-new` | `CGC_LAYER_AHEAD_PREFETCH=1` | ③ 證據文件掃描 | [log](../../Backup/layer_ahead/a4_ctrl.json)　[log](../../Backup/layer_ahead/a3_la.json)　[報告](../../Backup/layer_ahead/VERDICT_layer_ahead_2026-09-25.md) | [md](briefs/io-layer-ahead.md)　[html](briefs/io-layer-ahead.html) |
| 5 | [M-PF：背景 neighbour prefetch](briefs/io-mpf.md) | 4 | `prod-new` | `CGC_PREFILL_STREAM=1` | ③ 證據文件掃描 | [報告](../NEIGHBOUR_PREFETCH_VERDICT_2026-09-21.md) | [md](briefs/io-mpf.md)　[html](briefs/io-mpf.html) |
| 6 | [S1 早期診斷系列（09-16/17）](briefs/s1-refuted.md) | 4 | `prod-new` | `CGC_S1_TABLE_CHURN=1` | ③ 證據文件掃描 | [報告](../S1_SLOT_OWNER_REFUTED_20260917.html) | [md](briefs/s1-refuted.md)　[html](briefs/s1-refuted.html) |
| 7 | [3b：fill 觸發點搬出 hook ＋ batch 化](briefs/miss-3b.md) | 4 | `prod-new` | `（無自己的 option）` | ③ 證據文件掃描 | [log](../../Backup/seg_batch_s1_pairs/abba_212809.json)　[log](../../Backup/nofill_prod/nf_fill.json)　[報告](../DIAGNOSTIC_ARMS_LEDGER_2026-09-25.md) | [md](briefs/miss-3b.md)　[html](briefs/miss-3b.html) |
| 8 | [3c：per-expert 重算 kernel](briefs/miss-3c.md) | 4 | `prod-new` | `CGC_POOL_MADVISE=1` | ③ 證據文件掃描 | [報告](../STEP3_PER_EXPERT_RECOMPUTE_2026-09-24.md) | [md](briefs/miss-3c.md)　[html](briefs/miss-3c.html) |
| 9 | [RSL-MTP（draft top-8 ⊆ 已付費並集）＋ 練 draft head](briefs/mtp-rsl.md) | 4 | `prod25` | `CGC_P_ROUTE=1` | ③ 證據文件掃描 | [log](../../Backup/phase_decomp/reuse_distance_result.json)　[log](../../Backup/phase_decomp/spec_cost_curve_20260918_2030.json)　[報告](../MOE_MTP_FEASIBILITY_2026-09-18.md) | [md](briefs/mtp-rsl.md)　[html](briefs/mtp-rsl.html) |
| 10 | [verify residency thrash ／ 池配額 ／ prefetch 開關](briefs/mtp-verify-opt.md) | 4 | `prod25` | `CGC_NO_PREFETCH=1` | ③ 證據文件掃描 | [log](../../Backup/phase_decomp/spec_onoff/k_sweep.json)　[log](../../Backup/phase_decomp/spec_cost_curve_20260918_2030.json)　[報告](../EXPERT_CACHE_THRASH_2026-09-19.md) | [md](briefs/mtp-verify-opt.md)　[html](briefs/mtp-verify-opt.html) |
| 11 | [accept rule／dynamic-k／n_max 調整](briefs/mtp-accept.md) | 4 | `prod-new` | `CGC_MTP_REJECTION=1` | ③ 證據文件掃描 | [log](../../Backup/cgc_logs/mtp_rule_ab_g6_r2.json)　[log](../../Backup/cgc_logs/mtp_rule_ab_g6.json)　⚠ mtp_suite_accept_g6.json（不在工作區）　[報告](../MTP_ACCEPT_RULE_G6_2026-09-20.md) | [md](briefs/mtp-accept.md)　[html](briefs/mtp-accept.html) |
| 12 | [K0–K5 系列（融合／小 op 群）](briefs/k-series.md) | 4 | `prod25` | `（無自己的 option）` | ③ 證據文件掃描 | [報告](../K5_GRID_HYPOTHESIS_2026-09-21.md) | [md](briefs/k-series.md)　[html](briefs/k-series.html) |
| 13 | [G4：元素級融合 kernel](briefs/g4.md) | 4 | `prefill250` | `（無自己的 option）` | ③ 證據文件掃描 | [log](../../Backup/phase_decomp/metal_fusion_dispatch_cost_20260920_2053.json)　[log](../../Backup/cgc_logs/joint_step_accept_g6.json)　⚠ g4_null_manifest.json（不在工作區）　[報告](../G4_ADDFUSE_ALREADY_FUSED_2026-09-20.md) | [md](briefs/g4.md)　[html](briefs/g4.html) |
| 14 | [IOCACHE 約束承認 ＋ 段邊界 S2](briefs/io-constraint.md) | 4 | `prefill250` | `CGC_SUBMIT_AHEAD=1` | ③ 證據文件掃描 | [log](../../Backup/phase_decomp/l3prize_off.json)　[log](../../Backup/cgc_logs/joint_step_accept_g6.json)　⚠ summary_l3_prize_submit_ahead.json（不在工作區）　[報告](../IOCACHE_CONSTRAINT_ADMISSION_2026-09-22.md) | [md](briefs/io-constraint.md)　[html](briefs/io-constraint.html) |
| 15 | [M3／M4／M5 離開條件（09-17 期）](briefs/m3.md) | 4 | `prod-new` | `（無自己的 option）` | ③ 證據文件掃描 | [報告](../M3_VERDICT_2026-09-17.md) | [md](briefs/m3.md)　[html](briefs/m3.html) |
| 16 | [入口／索引／決策頁（非實驗）](briefs/na-entry.md) | na | `prod-new` | `（無自己的 option）` | ⑤ 非實驗結論 | [報告](../DIAGNOSTIC_ARMS_LEDGER_2026-09-25.md) | [md](briefs/na-entry.md)　[html](briefs/na-entry.html) |
| 17 | [跨線／其他產品（Wan2.2、HarmonyOS、Windows client、Colibri、Unified IR…）](briefs/na-crossline.md) | na | `prod-new` | `（無自己的 option）` | ⑤ 非實驗結論 | [報告](../archive/pre-consistency-metrics-2026-09-11/CGC_COLIBRI_HERMES_ROUTEPOLICY_V2_INTEGRATION.md) | [md](briefs/na-crossline.md)　[html](briefs/na-crossline.html) |
| 18 | [舊口徑數據報告（Gemma4／MTP_BENCHMARK_WIN8GB／TPOT 路線圖）](briefs/na-olddata.md) | 4 | `prod25` | `CGC_SERVER_SKIP0=1` | ③ 證據文件掃描 | [報告](../archive/pre-consistency-metrics-2026-09-11/Gemma4_Final_Report.md) | [md](briefs/na-olddata.md)　[html](briefs/na-olddata.html) |

## 缺口（機械判定）

（無）

---

本檔由 `scripts/check/mindmap_profile_audit.py` 機械生成；改內容請改 `mindmap.json` 後重跑，勿直接編輯本檔。
