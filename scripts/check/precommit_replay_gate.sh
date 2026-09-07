#!/bin/bash
# precommit_replay_gate.sh — Pre-commit Replay Benchmark Gate
#
# 功能:
#   1. 檢查是否為代碼 commit（如果只是文檔 commit，跳過）
#   2. 確認 server 正在運行（如果沒有，啟動 production 配置）
#   3. 運行三個 profile 的 replay 測試（qa-zh / longform-zh / coding）
#   4. 收集所有指標: 速度 / 品質 / 三因子 / 機器狀態
#   5. 保存到數據庫（data/replay_bench/）
#   6. 與 baseline 比較，判定是否通過
#   7. 如果不通過，阻止 commit
#
# 用法:
#   ./scripts/check/precommit_replay_gate.sh          # 運行完整 gate
#   ./scripts/check/precommit_replay_gate.sh --skip   # 跳過 gate（設置 ALLOW_REPLAY_BENCH=1 也可以）
#   ./scripts/check/precommit_replay_gate.sh --status # 只顯示當前狀態
#
# 環境變數:
#   ALLOW_REPLAY_BENCH=1     # 跳過 gate（bootstrap 或純文檔 commit）
#   RUN_REPLAY_BENCH=0       # 完全關閉 gate
#   REPLAY_BENCH_RUNS=3      # 每個 profile 跑幾次（預設 3）
#   REPLAY_BENCH_TIMEOUT=300 # 超時時間（秒，預設 300）
#   CGC_SERVER_PORT=8080     # server 端口（預設 8080）

set -euo pipefail

# 顏色輸出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 項目根目錄
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  CGC Pre-commit Replay Benchmark Gate${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 檢查是否跳過
if [ "${RUN_REPLAY_BENCH:-1}" = "0" ]; then
    echo -e "${YELLOW}[SKIP] RUN_REPLAY_BENCH=0，跳過 replay benchmark gate${NC}"
    exit 0
fi

if [ "${ALLOW_REPLAY_BENCH:-0}" = "1" ]; then
    echo -e "${YELLOW}[SKIP] ALLOW_REPLAY_BENCH=1，跳過 replay benchmark gate${NC}"
    exit 0
fi

# 檢查是否只是文檔 commit
if ! git diff --cached --name-only | grep -qE '\.(cpp|h|py|sh|metal|swift)$'; then
    echo -e "${YELLOW}[SKIP] 沒有代碼文件變更，跳過 replay benchmark gate${NC}"
    echo "  變更文件:"
    git diff --cached --name-only | head -5 | sed 's/^/    /'
    exit 0
fi

echo -e "${BLUE}[1/6] 檢查環境...${NC}"

# 獲取 git 信息
GIT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
GIT_VERSION=$(git describe --tags --exact-match 2>/dev/null || echo "dev-${GIT_COMMIT:0:8}")
echo "  commit: ${GIT_COMMIT:0:8}"
echo "  branch: $GIT_BRANCH"
echo "  version: $GIT_VERSION"

# 檢查 server 是否運行
SERVER_PORT="${CGC_SERVER_PORT:-8080}"
SERVER_PID=$(ps aux | grep "llama-server" | grep -v grep | awk '{print $2}' | head -1)

if [ -z "$SERVER_PID" ]; then
    echo -e "${YELLOW}[WARN] llama-server 未運行，嘗試啟動 production 配置...${NC}"
    if [ -x "./scripts/run_server.sh" ]; then
        ./scripts/run_server.sh --detach > /tmp/cgc_precommit_server_start.log 2>&1 &
        echo "  等待 server 啟動..."
        for i in $(seq 1 60); do
            if curl -s --noproxy '*"http://127.0.0.1:$SERVER_PORT/health" 2>/dev/null | grep -q "ok"; then
                SERVER_PID=$(ps aux | grep "llama-server" | grep -v grep | awk '{print $2}' | head -1)
                echo -e "${GREEN}  server 已啟動 (PID: $SERVER_PID)${NC}"
                break
            fi
            sleep 2
        done
    else
        echo -e "${RED}[ERROR] 找不到 run_server.sh，無法啟動 server${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}  server 正在運行 (PID: $SERVER_PID)${NC}"
fi

# 再次確認 server 健康
if ! curl -s --noproxy '*' "http://127.0.0.1:$SERVER_PORT/health" 2>/dev/null | grep -q "ok"; then
    echo -e "${RED}[ERROR] server 健康檢查失敗${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}[2/6] 收集機器狀態基線...${NC}"
python3 -c "
import sys
sys.path.insert(0, './scripts/check')
from replay_bench_database import get_system_state
state = get_system_state(server_pid=$SERVER_PID)
print(f'  overall_tier: {state[\"overall_tier\"]}')
print(f'  memory_free: {state[\"memory\"][\"free_gb\"]}GB ({state[\"memory\"][\"free_pct\"]}%)')
print(f'  cpu_idle: {state[\"cpu\"][\"idle_pct\"]}%')
print(f'  expected_decode_tps: {state[\"expected_decode_tps\"]}')
"

echo ""
echo -e "${BLUE}[3/6] 運行 replay benchmark（三個 profile）...${NC}"
RUNS="${REPLAY_BENCH_RUNS:-3}"
TIMEOUT="${REPLAY_BENCH_TIMEOUT:-300}"
BENCH_OUTPUT="/tmp/cgc_precommit_replay_$(date +%Y%m%d_%H%M%S).json"

python3 ./scripts/check/replay_server_profile.py \
    --all-profiles \
    --runs "$RUNS" \
    --server-pid "$SERVER_PID" \
    --reference ./scripts/check/replay_bench_reference.json \
    --bench-output "$BENCH_OUTPUT" \
    --db-save \
    --db-version "$GIT_VERSION" \
    --db-branch "$GIT_BRANCH" \
    --db-verdict pending \
    --timeout "$TIMEOUT" \
    2>&1 | tee /tmp/cgc_precommit_replay.log

REPLAY_EXIT_CODE=${PIPESTATUS[0]}

if [ $REPLAY_EXIT_CODE -ne 0 ]; then
    echo -e "${RED}[ERROR] replay benchmark 運行失敗 (exit code: $REPLAY_EXIT_CODE)${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}[4/6] 分析測試結果...${NC}"

# 提取關鍵指標（含三因子）
python3 -c "
import json
with open('$BENCH_OUTPUT') as f:
    data = json.load(f)

print('  === 各 Profile 結果 ===')
for profile in ['qa-zh', 'longform-zh', 'coding']:
    p = data['profiles'].get(profile, {})
    quality = p.get('quality', {}).get('score')
    decode_tps = p.get('speed', {}).get('decode_tps')
    prefill_tps = p.get('speed', {}).get('prefill_tps')
    draft_accept = p.get('speed', {}).get('draft_accept_pct')
    speed_vs_expected = p.get('speed', {}).get('speed_vs_expected')
    print(f'  {profile}:')
    print(f'    quality: {quality}')
    print(f'    decode_tps: {decode_tps}')
    print(f'    prefill_tps: {prefill_tps}')
    print(f'    draft_accept: {draft_accept}%')
    print(f'    speed_vs_expected: {speed_vs_expected}')
    
    # 三因子指標
    tf = p.get('three_factors', {})
    if tf.get('available'):
        f1 = tf.get('factor1_count_cold', {})
        f2 = tf.get('factor2_zero_slot', {})
        aux = tf.get('auxiliary', {})
        print(f'    --- 三因子指標 ---')
        print(f'    count_cold_pct: {f1.get(\"cold_rate_all_pct\")}%')
        print(f'    zero_slot_usage_pct: {f2.get(\"zero_slot_usage_rate_pct\")}%')
        print(f'    cache_hit_rate_pct: {aux.get(\"cache_hit_rate_pct\")}%')
        cold = f1.get('cold_rate_all_pct')
        if cold is not None:
            print(f'    expert_resident_hit_rate: {100 - cold:.2f}%')
        dp = tf.get('draft_prefetch', {})
        if dp:
            print(f'    draft_prefetch_hit_rate: {dp.get(\"draft_prefetch_hit_rate_pct\")}%')

print()
print('  === 聚合指標 ===')
agg = data.get('aggregate', {})
print(f'  avg_decode_tps: {agg.get(\"decode_tps\", {}).get(\"avg\")}')
print(f'  median_decode_tps: {agg.get(\"decode_tps\", {}).get(\"median\")}')
print(f'  min_quality: {agg.get(\"quality_score\", {}).get(\"min\")}')
print(f'  avg_quality: {agg.get(\"quality_score\", {}).get(\"avg\")}')

# Prefetch drop breakdown（12 個 drop point 分類計數）
pdb = data.get('prefetch_drop_breakdown')
if pdb and pdb.get('available'):
    print()
    print('  === Prefetch Drop Breakdown ===')
    print(f'  total_drops: {pdb.get(\"total\")}')
    drop_points = [
        ('#1 no_free_slot', pdb.get('no_free_slot', 0)),
        ('#2 dbuf_cap_skip', pdb.get('dbuf_cap_skip', 0)),
        ('#3 drain_cleared', pdb.get('drain_cleared', 0)),
        ('#6 bg_reassign_race', pdb.get('bg_reassign_race', 0)),
        ('#7 guard_reject', pdb.get('guard_reject', 0)),
        ('#9 one_shot_consumed', pdb.get('one_shot_consumed', 0)),
        ('#12 collect_skipped', pdb.get('collect_skipped', 0)),
    ]
    for name, count in drop_points:
        if count > 0:
            pct = pdb.get(name.split()[-1] + '_pct', 0)
            print(f'    {name}: {count} ({pct}%)')
    # 警告：如果 drop 總數過高，可能影響品質
    total = pdb.get('total', 0)
    if total > 1000:
        print(f'    ⚠️  WARNING: high prefetch drop count ({total}) may indicate capacity or timing issues')

# 聚合的機器狀態
sys_agg = agg.get('system_state', {})
if sys_agg:
    print()
    print('  === 機器狀態聚合 ===')
    tier_stats = sys_agg.get('tier', {})
    print(f'  system_tier (worst): {tier_stats.get(\"worst\")}')
    print(f'  memory_free_pct (avg): {sys_agg.get(\"memory_free_pct\", {}).get(\"avg\")}%')
    print(f'  cpu_idle_pct (avg): {sys_agg.get(\"cpu_idle_pct\", {}).get(\"avg\")}%')
    print(f'  speed_vs_expected: {sys_agg.get(\"speed_vs_expected\", {}).get(\"most_common\")}')
"

echo ""
echo -e "${BLUE}[5/6] 與 baseline 比較...${NC}"

# 檢查是否有 baseline
BASELINE_FILE=".replay_bench_baseline.json"
if [ -f "$BASELINE_FILE" ]; then
    echo "  baseline 文件: $BASELINE_FILE"
    
    # 運行比較
    python3 ./scripts/check/replay_bench_compare.py \
        --current "$BENCH_OUTPUT" \
        --baseline "$BASELINE_FILE" \
        --reference ./scripts/check/replay_bench_reference.json \
        --report /tmp/cgc_precommit_compare.json \
        2>&1 || COMPARE_EXIT=$?
    
    COMPARE_EXIT=${COMPARE_EXIT:-0}
    
    if [ $COMPARE_EXIT -eq 0 ]; then
        echo -e "${GREEN}  [PASS] 與 baseline 比較通過${NC}"
        VERDICT="pass"
    elif [ $COMPARE_EXIT -eq 1 ]; then
        echo -e "${RED}  [FAIL] 與 baseline 比較失敗${NC}"
        VERDICT="fail"
    else
        echo -e "${YELLOW}  [ERROR] 比較出錯${NC}"
        VERDICT="error"
    fi
else
    echo -e "${YELLOW}  [WARN] 沒有 baseline 文件 ($BASELINE_FILE)，跳過比較${NC}"
    echo "  提示: 運行 'cp $BENCH_OUTPUT $BASELINE_FILE' 設置 baseline"
    VERDICT="neutral"
fi

# 更新數據庫中的 verdict
echo ""
echo -e "${BLUE}[6/6] 更新數據庫 verdict...${NC}"
python3 -c "
import json
# 這裡可以更新數據庫中的 verdict
# 由於數據庫記錄是只追加的，我們在這裡記錄最終 verdict
print(f'  最終 verdict: $VERDICT')
"

echo ""
echo -e "${BLUE}========================================${NC}"
if [ "$VERDICT" = "pass" ] || [ "$VERDICT" = "neutral" ]; then
    echo -e "${GREEN}  Replay Benchmark Gate: PASS${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    echo "  測試結果已保存到數據庫: data/replay_bench/"
    echo "  完整報告: $BENCH_OUTPUT"
    echo ""
    exit 0
else
    echo -e "${RED}  Replay Benchmark Gate: FAIL${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    echo "  品質或性能出現退化，請檢查以上報告。"
    echo "  完整報告: $BENCH_OUTPUT"
    echo ""
    echo "  如果這是預期的變更，可以設置 ALLOW_REPLAY_BENCH=1 跳過:"
    echo "    ALLOW_REPLAY_BENCH=1 git commit ..."
    echo ""
    exit 1
fi
