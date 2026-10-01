#!/bin/bash
# k=2 vs k=3 paired certification, BENCH caliber — `docs/K3_PAIR_CERT_V2_BENCH_2026-09-23.md`.
#
# WHAT THIS IS: one pair = one k=2 launch + one k=3 launch of `prod_profile.py`, order rotated
# ABBA. The registered statistic is the paired arm-mean difference (k2 - k3, t/s); the registered
# judgment happens once, at a pre-registered n, via `k_swing_decompose.py --paired --planned-n N`.
#
# WHY THIS SCRIPT EXISTS AND NOT THE DOC'S COMMAND LINES. Running §4 of the protocol verbatim
# produces a wrong answer three times over, and none of the three is loud:
#
#   (1) INTERPRETER. §4 says `python3`. On this box `python3` is 3.9.6, and `prod_profile.py`
#       imports `profile_duo.py`, which uses `float | None` (needs >= 3.10). It exits rc=1 with
#       `TypeError: unsupported operand type(s) for |` before argument parsing. Measured
#       2026-09-28. Hence $PY below, with a version guard.
#   (2) THE DELIVERY CELL IS UNREACHABLE, so this driver does NOT use it (2026-09-28 decision).
#       `llama_bench_matrix.py`'s `[cell contract]` is fail-closed on any cell that is not the
#       test card's §2.5 block (batch/ubatch 5632, prompt 2048, depths 512, gen 128, reps 3,
#       warm-skip 64), and it is ENTRY-INDEPENDENT -- verified zero-GPU 2026-09-28, the
#       delivery shape (batch 512, prompt 0) reports contract.ok=False through the matrix no
#       matter who calls it. `prod_profile.py` exists to measure exactly that shape, so since
#       the contract landed it cannot measure its own cell (see D8 in
#       docs/K3_PAIR_CERT_V2_EXECUTABILITY_2026-09-28.md).
#       `harness.py bench`'s `_BENCH_DEFAULTS` IS the §2.5 cell, and it exposes
#       `--spec-draft-n-max` (:1411), so it is the only entry that both passes the gate and can
#       express k. The caliber therefore CHANGED: this certifies k on `-p 2048 -b 5632`'s tg
#       row, not on the 12.57 delivery cell. Registered in
#       scripts/check/charters/exp-k3-pair-cert.yaml; the question was asked and answered before
#       the first launch, because a caliber cannot be swapped after the fact.
#   (3) UNREADABLE OUTPUT. §4 pipes the record into `k_swing_decompose.py`, whose loader only
#       understood the server caliber, and `prod_profile.py` did not carry llama-bench's per-rep
#       `samples_ts` at all — only mean±sd, and mean±sd of 3 does not determine the 3. Every arm
#       was skipped and the tool printed "fewer than two pairs" with rc=0. Both ends are fixed
#       (2026-09-28) and the loader now REFUSES (rc=2) instead of skipping silently.
#
# USAGE
#   scripts/check/k3_pair_cert.sh 1 4        # stage 1: the pilot, N1=4 pairs
#   scripts/check/k3_pair_cert.sh 5 8        # stage 2: whatever §3's rule registered
#
# The script only RUNS. It never prints t and never judges — `--sd-only` (pilot) and
# `--paired --planned-n N` (judgment) are separate calls on purpose, so sizing n and testing on
# the same pairs cannot happen by accident.
#
# ───────── POST-REBOOT RUNBOOK 2026-09-28（每步都自己停，不重試）─────────
# 一個 rep（兩支臂 × 3 次啟動）約 7.5 分鐘，而一條命令的視窗是 10 分鐘 ⇒ **一次跑一個 rep**，
# 四個 rep 四條命令。背景／detached 在這台盒子上撐不過命令邊界（實測：log 停在 60 秒），所以不要
# 用 `&`。每一條之前先看兩件事：沒有別人的引擎（pgrep）、窗口 admit。
#
#   0) 盒子：重開機後 free ≥ 5000 MiB 是**硬需求**（rep_split 的起跑狀態閘，見
#      docs/SWAP_GROWTH_GATE_2026-09-28.md §9：free < 5000 那一帶的臂 decode t/s 中位數低 ~10%，
#      所以它在跑前就拒）。若 free 掉到 5000 以下，寧可停下來清盒子，不要把它當「快一點就沒事」。
#   1) pgrep -f 'llama-bench|llama-server'   # 必須空
#   2) bash scripts/check/k3_pair_cert.sh 1 1     # 第一對（約 7.5 分鐘，必須 rc=0）
#   3) ... 2 2 / 3 3 / 4 4                       # 逐對；任一次 ABORT（rc=3）就停下來讀理由，
#                                                # 不要跳過那對往下跑（一個被跳過的 rep 會讓 ABBA 少一格）
#   4) /opt/homebrew/bin/python3 scripts/check/k3_swing_decompose.py --dir /tmp/kb \
#        --groups k2,k3 --sd-only                # 只估 sd，不判（這是階段 1 的全部產物）
#   5) 依 charter §53 的規則寫死 N = min(12, max(8, n_needed_at_point_sd))，然後跑 5..N
#      （階段 2），最後才做那**一次**：--paired --planned-n N
#
# 判決的注意事項（寫在這裡以免下一個 session 重踩）：
#   * 4 對 **不能**給出「有資格」的判決：階段 1 只估 sd，登記的 N 至少 8 對。
#   * F gate 的臨界值是**該設計自己** df 的分位數，而 df 隨臂數改變 ⇒ 這個數字會跟著階段走，
#     不能寫死。df=(n_arms−1, n_arms×(reps−1))：
#       階段 1 的四對（4 arms × 3 reps）  df=(3, 8)   ⇒ 95% 點 4.07
#       階段 2 的十二對（12 arms × 3 reps）df=(11, 24) ⇒ 95% 點 2.22
#     工具的預設（4,10 → 3.48）兩個都不是。
#     [實測 2026-09-28] 我第一版把 4.07 寫死在這裡並拿它讀 12 對的結果，工具當場印
#     「4.07 is the 95% point of a DIFFERENT df」——那是這條註解不該假設臂數的證據。
#     兩次讀數的結論相同（k2 F=1.58 < 2.22、k3 F=0.87 < 2.22），但結論相同是運氣，不是理由。
#   * 每一個 measured rep 都是**自己的啟動**（`delivery-repsplit` 孿生 cell），所以每個 rep 都有
#     自己的 draft liveness 與自己的記憶體判詞——那些都在 r{r}_{pos}_k{k}.json 的 rep_split 區塊裡。
#   * 已知的殘餘：session 內第一個 measured launch 系統性偏低（冷啟），ABBA 輪替只是把它在 rep
#     之間平均掉，不是移除；逐 rep 樣本有帶順序，判決時值得看一眼。

# `pipefail` IS LOAD-BEARING: the window gate below pipes the decision into `tee`, and without
# it `if ! cmd | tee` tests TEE's exit status -- so a refusing box would read as an admitting
# one. Measured 2026-09-28 running this script while `admits=False`: it proceeded to launch.
set -u -o pipefail
cd /Users/alexchuang/Documents/flashkv-devserver || exit 1

ROOT=$PWD
PY=/opt/homebrew/bin/python3          # >= 3.10 required; see (1) above
OUT=/tmp/kb
LOG=$OUT/driver.log
# NEED_MB: the SHARED `server_window.NEED_MB` (8000) is the bar for a full-mtp SERVER
# launcher -- a 13 GB mmap'd model plus its pool. This driver runs `llama-bench`, and
# `cmd_bench` itself applies NO memory bar at all (it gates on thermal cooldown and on the
# compressor). So the 8000 here was this script imposing a launcher-class bar on a bench run,
# which is this script's error, not a contract. 6500 is the owner's authorised compromise
# (2026-09-28) and it does NOT touch `server_window.NEED_MB`, so no other caller moves.
# The compressor term is still enforced -- that one is a flow problem, not a headroom one.
NEED_MB=6500
# The `delivery` cell declared in the test card §2.5.1 (2026-09-28). Two reasons it, and not
# the default cell: (i) the default cell's `-ub 5632` prefill hits Metal OOM on this box
# (`CGC-METAL-FAIL ... OutOfMemory`, reproduced twice) and its escape hatches (-ub, expert
# cache) are STRICT cell dimensions, so it cannot be worked around; (ii) this is the shape the
# 12.57 delivery number came from, so it is the one the k decision is about.
ARM="prod25:!CGC_PREFILL_STREAM=1;!CGC_GATHER_SLAB_CAP=256"   # == registry `prod25-stream`
# [CGC 2026-09-28] 認證現在走 rep-split session：一個 rep 一次啟動、之間冷卻（`rep_split.py`）。
# 理由與量到的事實：同一次 `-r 3` 啟動裡的三個 rep **不可交換**（rep2/3 的 draft 鏈整個死掉、
# 量到純解碼，而臂仍被讀成 k=3；`--delay` 冷不到 rep 之間，因為它在 reps 迴圈外），而 corpus 裡
# 唯一沒出現過死鏈的 k 結論正是「一個 rep 一次量」。`reps` 沒有被放寬 —— 卡片上宣告的是孿生
# cell `delivery-repsplit`（reps:1 + rep_split{of:delivery, launches:3, cool_to:NOMINAL}），
# `cell_contract` 會拒任何未宣告的 `reps=1`。
CELL=delivery-repsplit
CHARTER=scripts/check/charters/exp-k3-pair-cert.yaml
REPS=3                                # the cell's reps; also what gives per-rep samples

say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

[ $# -eq 2 ] || { echo "usage: $0 <r_start> <r_end>   (e.g. $0 1 4)"; exit 2; }
R0=$1; R1=$2
mkdir -p "$OUT" "$OUT/logs"

# --- guard (1): the interpreter. Probe the actual dependency rather than a version string:
#     the requirement is "this interpreter can import profile_duo", and a hand-written pattern
#     is one more thing that can be wrong (a first draft of this file used `3.[2-9]`, which
#     matches 3.9 -- the exact version being excluded).
if ! ERR=$("$PY" -c 'import sys;sys.path.insert(0,"scripts/check");import profile_duo' 2>&1); then
  echo "FATAL: $PY cannot import prod_profile's dependencies."
  echo "       $PY is Python $("$PY" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])' 2>&1)"
  echo "       ${ERR##*$'\n'}" | tail -1
  echo "       Do not fall back to bare 'python3' here: it is 3.9.6, and profile_duo.py:92"
  echo "       ('-> float | None') fails at import, before any argument is parsed."
  exit 2
fi

# --- guard (2): never overwrite an arm ---
for r in $(seq "$R0" "$R1"); do
  for f in "$OUT"/r${r}_??_k?.json; do
    [ -e "$f" ] && { echo "FATAL: $f already exists. A re-run is a NEW experiment — remove it"
                     echo "       deliberately or pick another rep range. Not overwriting."; exit 2; }
  done
done

# --- guard (3): the box. A foreign llama means the numbers belong to someone else's run, and
#     the launcher's own preflight would refuse (or, before 2026-09-18, SIGTERM theirs).
if ! "$PY" -c "
import sys; sys.path.insert(0,'$ROOT/scripts/check')
import server_window as sw
d = sw.decision(need_mb=$NEED_MB)
print('  window: admits=%s need_mb=%s reclaimable=%.0f free%%=%s foreign=%s' % (
      d['admits'], '$NEED_MB', d['reclaimable_mb'], d['launcher_free_pct'], d['foreign_llama']))
sys.exit(0 if d['admits'] else 1)
" | tee -a "$LOG"; then
  echo "FATAL: window does not admit (see above). Refusing to launch into a busy box." | tee -a "$LOG"
  exit 2
fi
pgrep -f 'llama-bench|llama-server' >/dev/null && {
  echo "FATAL: a llama process is already running; aborting rather than competing for the GPU."
  exit 2; }

arm() {  # $1 = k (2|3), $2 = rep, $3 = position a|b
  local k=$1 r=$2 pos=$3
  # EVERY dimension is passed explicitly, even though `_BENCH_DEFAULTS` matches the DEFAULT cell:
  # this run is on the `delivery` cell, whose dims differ (batch 512, prompt 0, ctx 4096,
  # fixed-fill-seed null), and letting a default decide WHICH cell we are on is the same class of
  # mistake as `--no-ref`. `--cell delivery` is what makes the contract check THIS cell instead
  # of refusing it. `--reps` is explicit because reps carry the per-rep samples the variance
  # split needs (§5.1: reps do not buy power, they buy identifiability).
  # `--spec-type draft-mtp` IS LOAD-BEARING, and its absence is silent. The whole point is the
  # draft length k, and `--spec-draft-n-max` is INERT without `--spec-type` (harness's own help
  # says so). Measured 2026-09-28: without it the matrix command carries no `--spec-type` at all,
  # llama-bench runs MTP OFF, and the record's `spec_draft_n_max` comes back None -- both arms
  # then measure the same non-MTP kernel and the k comparison is meaningless while looking fine.
  # The §2.5 CELL does not list spec_type (it is a bench-side convention layered on the cell),
  # so the contract still passes.
  # `--workdir` is PER ARM and is what makes the liveness gate below possible: llama-bench's
  # stderr log lands there, and it is the only place `CGC-BENCH-ACCEPT` (the per-phase draft
  # counts) is written. Without it every arm's log piles into the shared default
  # `/tmp/harness_bench`, where two arms of the same profile have IDENTICAL filenames and the
  # later arm silently overwrites the earlier one's evidence -- which is exactly what happened to
  # r1.a (2026-09-28). The abort message already pointed at `$OUT/logs/<arm>/` before this line
  # existed, i.e. at a directory the script never created.
  local logdir="$OUT/logs/r${r}_${pos}_k${k}"
  mkdir -p "$logdir"
  # 形狀參數以**一個字串**傳給 `rep_split.py --shape`（它用 shlex 切），它再原樣轉給每次啟動的
  # `harness bench`。刻意不在此處組陣列：`--reps` 由協定決定（=1），而 `--shape` 裡出現 `--reps`
  # 會被工具拒收 —— 這樣「一次啟動一個 rep」就不只是一句口號。
  # `--spec-type draft-mtp` 仍是 load-bearing（沒有它 `--spec-draft-n-max` 是惰性的，而 MTP 會
  # 靜默關著、兩支臂量到同一個 kernel）。
  local shape="--prompt 0 --batch 512 --ctx-size 4096 --warm-skip 64 --fixed-fill-seed 0 --spec-type draft-mtp"
  say "r${r}.${pos} k=${k} -> ${OUT}/r${r}_${pos}_k${k}.json（rep-split: ${REPS} 次啟動）"
  local t0; t0=$(date +%s)
  # k=3 passes NOTHING (llama-bench default is 3); k=2 passes the flag. The asymmetry is the
  # protocol's, recorded, not an accident. The k read-back below is what makes it safe: if the
  # flag ever stops reaching llama-bench, the arm is rejected instead of silently being k=3.
  # Two explicit branches, not an array expansion: under `set -u` on bash 3.2 (macOS) an EMPTY
  # array expansion is an unbound-variable error, and an empty array is exactly the k=3 case.
  # k=3 不傳旗標（llama-bench 預設 3），k=2 傳；下面的讀回驗證讓這個不對稱是安全的。
  if [ "$k" = "2" ]; then
    shape="$shape --spec-draft-n-max 2"
  fi
  "$PY" scripts/check/rep_split.py \
    --cell "$CELL" --arm "$ARM" --charter "$CHARTER" \
    --shape "$shape" --k "$k" \
    --out "$logdir" --workdir "$logdir/launches" \
    --json "$OUT/r${r}_${pos}_k${k}.json" >>"$LOG" 2>&1
  local rc=$? t1; t1=$(date +%s)
  say "r${r}.${pos} k=${k} rc=${rc} elapsed=$((t1-t0))s"
  [ "$rc" -ne 0 ] && say "!! rc=$rc — read the log before trusting this arm"

  # An arm that produced no row must stop the run, not be counted. `prod_profile.py` returns
  # rc=0 even when EVERY axis is `NO ROW` (measured 2026-09-28), so rc alone cannot catch it —
  # and 16 empty arms in 20 seconds would otherwise look like a completed stage 1.
  local why
  why=$(  "$PY" -c "
import json,sys
sys.path.insert(0,'$ROOT/scripts/check')
import k_swing_decompose as kw
p='$OUT/r${r}_${pos}_k${k}.json'
try:
    json.load(open(p))
except Exception as e:
    print('unreadable record: %s' % e); sys.exit(1)
if kw.arm_rows(p) is None:
    print(kw.unusable_reason(p) or 'no decode cell with per-rep samples')
    sys.exit(1)
# k is a property of the arm, so read it back from the record rather than trusting the flag.
rec=json.load(open(p))
want=int('$k')
got=(rec[0] if isinstance(rec,list) else rec).get('spec_draft_n_max')
got=3 if got is None else got
if got != want:
    print('arm says k=%s but was asked for k=%s -- the flag did not reach llama-bench' % (got,want))
    sys.exit(1)
print('')
" 2>&1)

  # --- per-REP draft liveness (2026-09-28). The `k` read-back above proves the FLAG reached
  #     llama-bench. It does NOT prove the draft chain was still drafting while the reps were
  #     being timed, and that is a different question with a different answer. Measured on both
  #     arms of pilot pair r1, and reproduced on a rerun that started at NOMINAL: after the FIRST
  #     measured rep the chain stops drafting entirely (drafted=0, mean_len=1.0000) and never
  #     recovers, with ~256-264x `llama_decode[0] returned -1`. A "k=3" arm is therefore ONE
  #     speculative rep plus TWO PLAIN DECODE reps, and its avg_ts/platform_ts blend two code
  #     paths. Both r1 arms passed the checks that existed and were compared anyway; this gate is
  #     what would have refused them (it refuses both, verified on their own logs).
  #
  #     This is the same lesson as the window gate at the top of the file: the guard existing is
  #     not the same as the guard being on the path.
  local live lrc
  live=$(  "$PY" scripts/check/draft_liveness.py --log-dir "$logdir" --expect-timed "$REPS" 2>&1 )
  lrc=$?
  if [ "$lrc" -ne 0 ]; then
    why="${why:+$why; }draft liveness (rc=$lrc): $(echo "$live" | tr '\n' ' ')"
  fi
  if [ -n "$why" ]; then
    say "K3CERT ABORT: r${r}.${pos} k=${k} produced no usable arm — $why"
    say "  the record is at $OUT/r${r}_${pos}_k${k}.json ; llama-bench stderr at $logdir/"
    say "  (a run of empty arms is not a run of measurements — stopping rather than continuing)"
    exit 3
  fi
  return 0
}

# ABBA: odd reps put k2 first, even reps put k3 first.
for r in $(seq "$R0" "$R1"); do
  if [ $((r % 2)) -eq 1 ]; then arm 2 "$r" a; arm 3 "$r" b
  else                         arm 3 "$r" a; arm 2 "$r" b; fi
done

say "=== done: reps ${R0}..${R1}"
N=$("$PY" -c "
import glob, os
reps=set()
for p in glob.glob('$OUT/r*_*_k?.json'): reps.add(os.path.basename(p).split('_')[0])
print(len(reps))")

cat <<EOF | tee -a "$LOG"
------------------------------------------------------------------------------------------------
pairs on disk: $N

NEXT (pilot, withholds t by design):
  $PY scripts/check/k_swing_decompose.py --dir $OUT --groups k2,k3 --sd-only

THEN, after registering N per §3's rule (min(12, max(8, n_needed_at_point_sd))):
  $PY scripts/check/k_swing_decompose.py --dir $OUT --groups k2,k3 --paired --planned-n N

The F gate is a quantile of THIS design's df, and df moves with the arm count:
df=(n_arms-1, n_arms*(reps-1)) -- 4 arms x 3 reps is df=(3,8) -> 4.07, but 12 arms x 3 reps is
df=(11,24) -> 2.22. Neither is the tool's (4,10) default of 3.48. Pass the value for the stage
you are actually reading, or read the "!! F gate" note it prints.
------------------------------------------------------------------------------------------------
EOF
