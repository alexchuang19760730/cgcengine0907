#!/bin/sh
# [2026-09-26 14:5x] 补齐最後一格：n_main=32 的 union/gap 拆分
#
# 已知：CGC_CB_N_MAIN=32 ⇒ step −11.4% / tg +12.8%（D5 長探針 1045/1045 PASS）。
# 未知：這 −11.4% 砍在哪個桶？
#     gap   ↓ ⇒ 段間空窗縮小 = 直接命中目標函數（operator 的目標就是消 gap）
#     union ↓ ⇒ 同工作量下 GPU 更有效率（buffer 間重疊被收緊）
#     busy  ↓ ⇒ 真工作量變了（不該發生：n_main 只改「誰編碼」，不改內容 ⇒ 若變了要查）
#     wait  ↓ ⇒ 段完成輪詢總耗時下降（CPU 側）
#
# 臂序 ABBA：ctl64a → nm32a → nm32b → ctl64b
#   控制臂放頭尾 ⇒ 控制漂移界；處理臂連兩次 ⇒ 處理臂噪聲界（單臂噪音底 ≈ ±27%）
# 四臂全帶：CGC_DECODE_PROFILE=1;CGC_HOOK_SPLIT=1;CGC_GPU_TIMING=1
#   CGC_GPU_TIMING 零擾動（只讀 Metal 自記的 GPUStartTime/GPUEndTime，不插 barrier）
# 0 重建：CGC_CB_N_MAIN 與 CGC_GPU_TIMING 皆已在 binary（sweep3 已證，97 行/臂）
# 可續跑：已有 non-empty bench.json 就 SKIP
set -u
cd /Users/alexchuang/Documents/flashkv-devserver || exit 1
PY=/Users/alexchuang/.workbuddy/binaries/python/versions/3.13.12/bin/python3
WD=Backup/phase_decomp/cbnmain_nm32
B="CGC_DECODE_PROFILE=1;CGC_HOOK_SPLIT=1;CGC_GPU_TIMING=1"
rm -f "${WD}/DONE"

# ---- 窗口門（會 abort 的閘門，不是印出來就好）----
# ★ 2026-09-26 15:0x 加固：**單次乾淨 ≠ 有空窗**。14:58:17 本腳本單次通過，卻正好落在
#   別的 session（`/tmp/armed_fc.sh`，paired MTP）**兩臂之間的間隙** ⇒ 擠進別人實驗中段、
#   thermal 從 1 升到 2。所以改成：① 要**連續 3 次**（間隔 20s）都乾淨；
#   ② 乾淨的定義加上 **thermal == NOMINAL**（別人的 GPU 工作必然發熱 => 這是最可靠的第三方信號）；
#   ③ 加上別條線已知的布防名（armed_fc / frag_orchestrate）。
need=3; ok=0
for i in $(seq 1 90); do
  busy=0
  lsof -nP -iTCP:8080 -sTCP:LISTEN >/dev/null 2>&1 && busy=1
  pgrep -x llama-server >/dev/null 2>&1 && busy=1
  pgrep -x llama-bench  >/dev/null 2>&1 && busy=1
  pgrep -f '[s]pec_cost_curve|[h]arness.py|[d]ecode_sweep|[r]un_ids_dst_capture|[a]rmed_fc|[f]rag_orchestrate' >/dev/null 2>&1 && busy=1
  lv=$(notifyutil -g com.apple.system.thermalpressurelevel 2>/dev/null | awk '{print $2}')
  [ "${lv}" != "0" ] && busy=1
  if [ "${busy}" = 0 ]; then
    ok=$((ok + 1))
    echo "  clean ${ok}/${need} (check ${i}/90) thermal=${lv} at $(date +%H:%M:%S)"
    if [ "${ok}" -ge "${need}" ]; then echo "WINDOW FREE (${need} consecutive clean) at $(date +%H:%M:%S)"; break; fi
  else
    ok=0
    echo "  busy (check ${i}/90) thermal=${lv} at $(date +%H:%M:%S)"
  fi
  sleep 20
done
if [ "${ok}" -lt "${need}" ]; then echo "STILL_BUSY $(date +%H:%M:%S)"; date "+%Y-%m-%d %H:%M:%S" > "${WD}/REFUSED_WINDOW"; exit 3; fi

# ---- 壓縮機安靜度閘（存量 swap 只是標籤，流量才是閘）----
if [ -f scripts/check/compressor_pressure.py ]; then
  echo "--- compressor ---"
  ${PY} scripts/check/compressor_pressure.py --seconds 6 2>&1 | tail -3 || true
fi

# ---- ★ fail-closed thermal 閘（2026-09-26 15:0x 加；教訓見 gate.py 檔頭）----
# harness 內部的 thermal gate 是 fail-OPEN（超時照跑、只標 not quotable）⇒ 這裡補 fail-closed。
# ⚠ 拒跑時**不寫 DONE**（寫 REFUSED_THERMAL），免得等待器把「拒跑」讀成「跑完」。
echo "--- thermal gate (fail-closed, max 600s) ---"
if [ -f "${WD}/gate.py" ]; then
  if ! ${PY} "${WD}/gate.py" 600; then
    echo "REFUSED_THERMAL $(date +%H:%M:%S)"
    date "+%Y-%m-%d %H:%M:%S" > "${WD}/REFUSED_THERMAL"
    exit 3
  fi
fi

run_arm () {
  tag="$1"; extra="$2"
  if [ -s "${WD}/${tag}/bench.json" ]; then
    echo "===== ARM ${tag} SKIP (bench.json exists) ====="
  else
    echo "===== ARM ${tag} $(date +%H:%M:%S) ====="
    if [ -z "${extra}" ]; then
      ${PY} scripts/check/harness.py bench --arm "prod-new:${B}" \
        --workdir "${WD}/${tag}" --json "${WD}/${tag}/bench.json" > "${WD}/${tag}.log" 2>&1
    else
      ${PY} scripts/check/harness.py bench --arm "prod-new:${B};${extra}" \
        --workdir "${WD}/${tag}" --json "${WD}/${tag}/bench.json" > "${WD}/${tag}.log" 2>&1
    fi
  fi
  tg=$(grep -aoE "tg p=0 +n=[0-9]+ +d=512 +-> +[0-9.]+" "${WD}/${tag}.log" 2>/dev/null | grep -oE "[0-9.]+$" | head -1)
  pp=$(grep -aoE "pp p=2048 +n=0 +d=512 +-> +[0-9.]+" "${WD}/${tag}.log" 2>/dev/null | grep -oE "[0-9.]+$" | head -1)
  g=$(grep -ac "^CGC-GPUTIME: step=" "${WD}/${tag}"/*.stderr.log 2>/dev/null || echo 0)
  echo "  ${tag} pp=${pp} tg=${tg} gputime_lines=${g}"
}

run_arm ctl64a ""
run_arm nm32a  "CGC_CB_N_MAIN=32"
run_arm nm32b  "CGC_CB_N_MAIN=32"
run_arm ctl64b ""

echo "===== DONE $(date +%H:%M:%S) ====="
date "+%Y-%m-%d %H:%M:%S" > "${WD}/DONE"
${PY} "${WD}/analyze.py" 2>&1 | tee "${WD}/analyze.txt" || true
