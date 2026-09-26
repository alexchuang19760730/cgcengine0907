#!/bin/sh
# [2026-09-26 22:3x] ★ 相鄰一對（`off1,on1` 或 `on2,off2`）—— 為「**可比的 t/s**」而設計。
#
# 為什麼不是一支腳本跑四臂：16 GB 上連跑會**累積 swap**（每臂 +8 GB、而存量不自動回落
# ⇒ 第 3~4 臂必 OOM）。2026-09-26 兩次實踩，`min_free` 都是 14.09 / 14.14 MiB（macOS 硬底）。
# ⚠ `purge` **救不了**：它不減 `swap_used`（`MEMORY_HYGIENE.md`「起跑前流程」已記）。
# ⇒ 對策：**一支腳本只跑相鄰 2 臂**，並在**臂與臂之間**檢查 swap；超預算就**主動停手**（不賭 OOM）。
#
# 完整 ABBA 由兩支合起來（**兩段共用同一個 WD**，最後一起分析）：
#     sh run_pair.sh off1,on1      # 段 1（off 先 ⇒ 若機器在變快，對 on 不利 ⇒ 保守）
#     ← 段間清記憶體：**重開機**（唯一可靠的，purge 不行）
#     sh run_pair.sh on2,off2      # 段 2（方向相反 ⇒ ABBA 抵銷臂序漂移）
#     python3 ../cbnmain_nm32/analyze.py . off1,on1,on2,off2
#
# 環境變數：SWAP_STOP_MIB（預設 11000）＝臂間 swap 上限，超過就 STOPPED_SWAP 停手。
set -u
cd /Users/alexchuang/Documents/flashkv-devserver || exit 1
PY=/Users/alexchuang/.workbuddy/binaries/python/versions/3.13.12/bin/python3
ORDER="${1:?usage: run_pair.sh off1,on1 | on2,off2}"
TAG=$(printf '%s' "${ORDER}" | tr ',' '_')
SEQ=$(printf '%s' "${ORDER}" | tr ',' ' ')
WD="Backup/phase_decomp/cbnmain_pair"
mkdir -p "${WD}"
B="CGC_DECODE_PROFILE=1;CGC_HOOK_SPLIT=1;CGC_GPU_TIMING=1"
# ★ 臂間策略（2026-09-26 22:44 修正）：**等 swap 回落**，不是「看到高就停手」。
#   實測：`off1` 跑完時 swap=11562 MiB，**6 分鐘後自然回到 3188 MiB**
#   —— 引擎退出後 macOS 會回收它的匿名頁。舊版一看到 11562 就 STOPPED_SWAP，
#   把「正常暫態」誤判成「髒窗口」。真正要防的是**帶著高 swap 起跑**。
SWAP_CALM_MIB="${SWAP_CALM_MIB:-6000}"     # 起跑前的 swap 上限（回落目標）
SWAP_WAIT_MAX_S="${SWAP_WAIT_MAX_S:-600}"   # 等回落的最長時間（秒）
rm -f "${WD}/DONE_${TAG}" "${WD}/REFUSED_THERMAL_${TAG}" "${WD}/REFUSED_WINDOW_${TAG}" "${WD}/STOPPED_SWAP_${TAG}"

swap_used_mib () {
  sysctl -n vm.swapusage | sed -n 's/.*used = \([0-9.]*\)M.*/\1/p' | cut -d. -f1
}

wait_swap_calm () {
  t0=$(date +%s)
  while :; do
    sw=$(swap_used_mib)
    case "${sw}" in ''|*[!0-9]*) sw=0 ;; esac
    if [ "${sw}" -le "${SWAP_CALM_MIB}" ]; then echo "  swap calm: ${sw} MiB"; return 0; fi
    now=$(date +%s)
    if [ $((now - t0)) -ge "${SWAP_WAIT_MAX_S}" ]; then echo "  swap 未回落: ${sw} MiB"; return 1; fi
    echo "  waiting swap: ${sw} MiB > ${SWAP_CALM_MIB} at $(date +%H:%M:%S)"
    sleep 20
  done
}

# ---- 窗口門：連續 3 次乾淨；乾淨的定義含 thermal == NOMINAL（教訓見 MEMORY_HYGIENE）----
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
    echo "  clean ${ok}/${need} (check ${i}/90) thermal=${lv} swap=$(swap_used_mib)MiB at $(date +%H:%M:%S)"
    if [ "${ok}" -ge "${need}" ]; then echo "WINDOW FREE at $(date +%H:%M:%S)"; break; fi
  else
    ok=0
    echo "  busy (check ${i}/90) thermal=${lv} at $(date +%H:%M:%S)"
  fi
  sleep 20
done
if [ "${ok}" -lt "${need}" ]; then echo "STILL_BUSY"; date "+%F %T" > "${WD}/REFUSED_WINDOW_${TAG}"; exit 3; fi

# ---- fail-closed thermal（非 NOMINAL 就拒跑）----
echo "--- thermal gate ---"
if ! ${PY} "${PWD}/Backup/phase_decomp/cbnmain_nm32/gate.py" 600; then
  echo "REFUSED_THERMAL"; date "+%F %T" > "${WD}/REFUSED_THERMAL_${TAG}"; exit 3
fi

run_arm () {
  tag="$1"                                  # off1 / on1 / on2 / off2
  case "${tag}" in
    on*) extra="CGC_CB_N_MAIN=32" ;;
    *)   extra="" ;;
  esac
  if [ -s "${WD}/${tag}/bench.json" ]; then
    echo "===== ARM ${tag} SKIP (bench.json 已存在) ====="
  else
    echo "===== ARM ${tag} $(date +%H:%M:%S)  swap=$(swap_used_mib)MiB ====="
    if [ -z "${extra}" ]; then
      ${PY} scripts/check/harness.py bench --arm "prod-new:${B}" \
        --workdir "${WD}/${tag}" --json "${WD}/${tag}/bench.json" > "${WD}/${tag}.log" 2>&1
    else
      ${PY} scripts/check/harness.py bench --arm "prod-new:${B};${extra}" \
        --workdir "${WD}/${tag}" --json "${WD}/${tag}/bench.json" > "${WD}/${tag}.log" 2>&1
    fi
    echo "  harness rc=$?"
  fi
  tg=$(grep -aoE "tg p=0 +n=[0-9]+ +d=512 +-> +[0-9.]+" "${WD}/${tag}.log" 2>/dev/null | grep -oE "[0-9.]+$" | head -1)
  pp=$(grep -aoE "pp p=2048 +n=0 +d=512 +-> +[0-9.]+" "${WD}/${tag}.log" 2>/dev/null | grep -oE "[0-9.]+$" | head -1)
  g=$(grep -ac "^CGC-GPUTIME: step=" "${WD}/${tag}"/*.stderr.log 2>/dev/null || echo 0)
  echo "  ${tag}: tg=${tg} pp=${pp} gputime_lines=${g} swap=$(swap_used_mib)MiB"
  swap_used_mib > "${WD}/.swap_after_${tag}"
}

n=0
for arm in ${SEQ}; do
  n=$((n + 1))
  # 每一臂起跑前都等 swap 回落（含第一臂）—— 防的是「帶著高 swap 起跑」⇒ 峰值耗盡 free ⇒ OOM
  echo "--- 起跑前等 swap 回落（門檻 ${SWAP_CALM_MIB} MiB，最多 ${SWAP_WAIT_MAX_S}s）---"
  if ! wait_swap_calm; then
    echo "STOPPED_SWAP_TIMEOUT: swap 未回落到 ${SWAP_CALM_MIB} ⇒ 停手（不賭 OOM）"
    date "+%F %T" > "${WD}/STOPPED_SWAP_${TAG}"
    exit 4
  fi
  run_arm "${arm}"
done

echo "===== PAIR DONE $(date +%H:%M:%S) ====="
date "+%F %T" > "${WD}/DONE_${TAG}"
