#!/bin/bash
# batch_retest_commits.sh - 批量重測歷史 commit
#
# 功能:
#   1. 批量 checkout 多個 commit
#   2. 每個 commit 重新編譯
#   3. 運行三個 profile 的 replay 測試
#   4. 收集所有指標（速度、品質、三因子、機器狀態）
#   5. 保存到數據庫
#   6. 生成對比報告
#
# 用法:
#   ./scripts/check/batch_retest_commits.sh --commits "b8a564d45 32cff26aa"
#   ./scripts/check/batch_retest_commits.sh --priority P0
#   ./scripts/check/batch_retest_commits.sh --all
#   ./scripts/check/batch_retest_commits.sh --list
#
# 環境變數:
#   RETEST_RUNS=3          # 每個 profile 跑幾次
#   RETEST_POOL=8GB         # pool 大小 (4GB/8GB)
#   RETEST_SKIP_BUILD=0     # 跳過編譯（用於測試）
#   RETEST_DRY_RUN=0        # 只顯示計劃，不實際執行

set -euo pipefail

# 顏色輸出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 項目根目錄
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# 預設配置
RUNS="${RETEST_RUNS:-3}"
POOL="${RETEST_POOL:-8GB}"
SKIP_BUILD="${RETEST_SKIP_BUILD:-0}"
DRY_RUN="${RETEST_DRY_RUN:-0}"

# commit 清單（按優先級分組）
COMMIT_GROUP_P0="b8a564d45 32cff26aa"
COMMIT_GROUP_P1="13b78a08f 10bdf0128 a23195e01"
COMMIT_GROUP_P2="df3ee17a6 9e046443e fc129b3a9"
COMMIT_GROUP_P3="ef5eea24c 7a2a0c2fe"

# 獲取 commit 描述
get_commit_desc() {
    case "$1" in
        b8a564d45) echo "P2 GEMV coalescing [量產基礎, 25.17 t/s]" ;;
        32cff26aa) echo "6 tunable env var + 參數掃描 [最高速度, 25.87 t/s]" ;;
        13b78a08f) echo "EMA alpha default 0.75 (tuned)" ;;
        10bdf0128) echo "DBUF spike: crash-free hook-time refill [21.7 t/s]" ;;
        a23195e01) echo "EMA-victim + 4/8GB A/B [波動最小]" ;;
        df3ee17a6) echo "SpAc EMA estimator port [gated, default off]" ;;
        9e046443e) echo "pool budget default 4GiB -> 8GiB" ;;
        fc129b3a9) echo "歷史 4GB 成功 [speculative-simple, 24.96 t/s]" ;;
        ef5eea24c) echo "Phase-0 honest measurement [品質閘門修復]" ;;
        7a2a0c2fe) echo "WIP: MTP Draft Prefetch [關鍵新功能]" ;;
        *) echo "未知" ;;
    esac
}

# 獲取優先級分組
get_commit_group() {
    case "$1" in
        P0) echo "$COMMIT_GROUP_P0" ;;
        P1) echo "$COMMIT_GROUP_P1" ;;
        P2) echo "$COMMIT_GROUP_P2" ;;
        P3) echo "$COMMIT_GROUP_P3" ;;
        *) echo "" ;;
    esac
}

# 顯示用法
usage() {
    echo "用法: $0 [選項]"
    echo ""
    echo "選項:"
    echo "  --commits <hash1 hash2>   指定要測試的 commit"
    echo "  --priority <P0|P1|P2|P3>  按優先級測試"
    echo "  --all                      測試所有 commit"
    echo "  --list                     列出所有可測試的 commit"
    echo "  --runs <N>                 每個 profile 跑幾次 (預設: 3)"
    echo "  --pool <4GB|8GB>           pool 大小 (預設: 8GB)"
    echo "  --skip-build               跳過編譯"
    echo "  --dry-run                  只顯示計劃，不實際執行"
    echo "  -h, --help                 顯示幫助"
    echo ""
    echo "環境變數:"
    echo "  RETEST_RUNS, RETEST_POOL, RETEST_SKIP_BUILD, RETEST_DRY_RUN"
    exit 0
}

# 列出所有 commit
list_commits() {
    echo -e "${BLUE}============================================================${NC}"
    echo -e "${BLUE}  可測試的 commit 清單${NC}"
    echo -e "${BLUE}============================================================${NC}"
    echo ""
    
    for group in P0 P1 P2 P3; do
        echo -e "${YELLOW}【${group}】${NC}"
        for commit in $(get_commit_group "$group"); do
            desc=$(get_commit_desc "$commit")
            echo "  $commit - $desc"
        done
        echo ""
    done
}

# 解析參數
COMMITS_TO_TEST=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --commits)
            shift
            COMMITS_TO_TEST="$1"
            shift
            ;;
        --priority)
            shift
            COMMITS_TO_TEST=$(get_commit_group "$1")
            if [ -z "$COMMITS_TO_TEST" ]; then
                echo -e "${RED}[ERROR] 未知優先級: $1${NC}"
                exit 1
            fi
            shift
            ;;
        --all)
            COMMITS_TO_TEST="$(get_commit_group P0) $(get_commit_group P1) $(get_commit_group P2) $(get_commit_group P3)"
            shift
            ;;
        --list)
            list_commits
            exit 0
            ;;
        --runs)
            shift
            RUNS="$1"
            shift
            ;;
        --pool)
            shift
            POOL="$1"
            shift
            ;;
        --skip-build)
            SKIP_BUILD=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo -e "${RED}[ERROR] 未知參數: $1${NC}"
            usage
            ;;
    esac
done

# 檢查是否有指定 commit
if [ -z "$COMMITS_TO_TEST" ]; then
    echo -e "${RED}[ERROR] 請指定要測試的 commit (--commits, --priority, 或 --all)${NC}"
    echo ""
    list_commits
    exit 1
fi

# 轉換 pool 大小
if [ "$POOL" = "4GB" ]; then
    POOL_BYTES="4294967296"
elif [ "$POOL" = "8GB" ]; then
    POOL_BYTES="8589934592"
else
    echo -e "${RED}[ERROR] 未知 pool 大小: $POOL${NC}"
    exit 1
fi

# 顯示測試計劃
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}  批量重測計劃${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo "  Pool 大小: $POOL ($POOL_BYTES bytes)"
echo "  每個 profile 跑: $RUNS 次"
echo "  跳過編譯: $SKIP_BUILD"
echo "  Dry run: $DRY_RUN"
echo ""
echo "  要測試的 commit:"
for commit in $COMMITS_TO_TEST; do
    desc=$(get_commit_desc "$commit")
    echo "    - $commit: $desc"
done
echo ""

if [ "$DRY_RUN" = "1" ]; then
    echo -e "${YELLOW}[DRY RUN] 只顯示計劃，不實際執行${NC}"
    exit 0
fi

# 保存當前分支
ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo -e "${BLUE}  原始分支: $ORIGINAL_BRANCH${NC}"
echo ""

# 結果目錄
RESULT_DIR="/tmp/cgc_batch_retest_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RESULT_DIR"
echo -e "${BLUE}  結果目錄: $RESULT_DIR${NC}"
echo ""

# 測試結果彙總
SUMMARY_FILE="$RESULT_DIR/summary.md"
echo "# CGC Batch Retest Summary" > "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"
echo "測試時間: $(date)" >> "$SUMMARY_FILE"
echo "Pool 大小: $POOL" >> "$SUMMARY_FILE"
echo "每個 profile 跑: $RUNS 次" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"
echo "| Commit | 描述 | Decode TPS | Quality | Draft Accept | Count Cold | 機器狀態 | 結果 |" >> "$SUMMARY_FILE"
echo "|--------|------|------------|---------|--------------|------------|----------|------|" >> "$SUMMARY_FILE"

# 開始測試每個 commit
for commit in $COMMITS_TO_TEST; do
    desc=$(get_commit_desc "$commit")
    
    echo -e "${BLUE}============================================================${NC}"
    echo -e "${BLUE}  測試 commit: $commit${NC}"
    echo -e "${BLUE}  描述: $desc${NC}"
    echo -e "${BLUE}============================================================${NC}"
    echo ""
    
    # Checkout commit
    echo -e "${YELLOW}[1/5] Checkout commit $commit...${NC}"
    git checkout "$commit" 2>&1 | tail -5
    echo ""
    
    # 編譯
    if [ "$SKIP_BUILD" = "0" ]; then
        echo -e "${YELLOW}[2/5] 編譯...${NC}"
        cd src/llama.cpp
        if [ -f Makefile ]; then
            make -j$(sysctl -n hw.ncpu) 2>&1 | tail -10
        elif [ -f CMakeLists.txt ]; then
            mkdir -p build && cd build
            cmake .. -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -5
            make -j$(sysctl -n hw.ncpu) 2>&1 | tail -10
            cd ..
        fi
        cd "$PROJECT_ROOT"
        echo "  編譯完成"
    else
        echo -e "${YELLOW}[2/5] 跳過編譯${NC}"
    fi
    echo ""
    
    # 啟動 server
    echo -e "${YELLOW}[3/5] 啟動 server ($POOL pool)...${NC}"
    CGC_EXPERT_CACHE_BYTES="$POOL_BYTES" ./scripts/run_server.sh --detach > "$RESULT_DIR/${commit}_server.log" 2>&1 &
    SERVER_PID=$!
    
    # 等待 server 啟動
    echo "  等待 server 啟動..."
    for i in $(seq 1 60); do
        if curl -s --noproxy '*' "http://127.0.0.1:8080/health" 2>/dev/null | grep -q "ok"; then
            echo -e "${GREEN}  server 已啟動${NC}"
            break
        fi
        sleep 2
    done
    echo ""
    
    # 運行 replay 測試
    echo -e "${YELLOW}[4/5] 運行 replay 測試（3 個 profile，每個 $RUNS 次）...${NC}"
    BENCH_OUTPUT="$RESULT_DIR/${commit}_replay.json"
    
    python3 ./scripts/check/replay_server_profile.py \
        --all-profiles \
        --runs "$RUNS" \
        --reference ./scripts/check/replay_bench_reference.json \
        --bench-output "$BENCH_OUTPUT" \
        --db-save \
        --db-version "$commit" \
        --db-verdict pending \
        2>&1 | tee "$RESULT_DIR/${commit}_replay.log"
    
    REPLAY_EXIT=${PIPESTATUS[0]}
    echo ""
    
    # 提取關鍵指標
    echo -e "${YELLOW}[5/5] 提取關鍵指標...${NC}"
    if [ -f "$BENCH_OUTPUT" ]; then
        DECODE_TPS=$(python3 -c "
import json
with open('$BENCH_OUTPUT') as f:
    data = json.load(f)
coding = data.get('profiles', {}).get('coding', {})
print(coding.get('speed', {}).get('decode_tps', 'N/A'))
" 2>/dev/null || echo "N/A")
        
        QUALITY=$(python3 -c "
import json
with open('$BENCH_OUTPUT') as f:
    data = json.load(f)
agg = data.get('aggregate', {})
print(agg.get('quality_score', {}).get('min', 'N/A'))
" 2>/dev/null || echo "N/A")
        
        DRAFT_ACCEPT=$(python3 -c "
import json
with open('$BENCH_OUTPUT') as f:
    data = json.load(f)
coding = data.get('profiles', {}).get('coding', {})
print(coding.get('speed', {}).get('draft_accept_pct', 'N/A'))
" 2>/dev/null || echo "N/A")
        
        COUNT_COLD=$(python3 -c "
import json
with open('$BENCH_OUTPUT') as f:
    data = json.load(f)
coding = data.get('profiles', {}).get('coding', {})
tf = coding.get('three_factors', {})
f1 = tf.get('factor1_count_cold', {})
print(f1.get('cold_rate_all_pct', 'N/A'))
" 2>/dev/null || echo "N/A")
        
        SYSTEM_TIER=$(python3 -c "
import json
with open('$BENCH_OUTPUT') as f:
    data = json.load(f)
coding = data.get('profiles', {}).get('coding', {})
ss = coding.get('system_state', {})
print(ss.get('overall_tier', 'N/A'))
" 2>/dev/null || echo "N/A")
        
        # 判定結果
        VERDICT="PASS"
        if [ "$DECODE_TPS" != "N/A" ] && [ "$(echo "$DECODE_TPS < 25" | bc -l 2>/dev/null || echo 0)" = "1" ]; then
            VERDICT="FAIL"
        fi
        if [ "$QUALITY" != "N/A" ] && [ "$(echo "$QUALITY < 0.9" | bc -l 2>/dev/null || echo 0)" = "1" ]; then
            VERDICT="FAIL"
        fi
        
        echo "  Decode TPS: $DECODE_TPS"
        echo "  Quality: $QUALITY"
        echo "  Draft Accept: $DRAFT_ACCEPT%"
        echo "  Count Cold: $COUNT_COLD%"
        echo "  機器狀態: $SYSTEM_TIER"
        echo "  結果: $VERDICT"
        
        # 寫入 summary
        echo "| $commit | $desc | $DECODE_TPS | $QUALITY | $DRAFT_ACCEPT% | $COUNT_COLD% | $SYSTEM_TIER | $VERDICT |" >> "$SUMMARY_FILE"
    else
        echo -e "${RED}  [ERROR] 測試輸出文件不存在${NC}"
        echo "| $commit | $desc | ERROR | ERROR | ERROR | ERROR | ERROR | FAIL |" >> "$SUMMARY_FILE"
    fi
    echo ""
    
    # 關閉 server
    echo -e "${YELLOW}  關閉 server...${NC}"
    pkill -f "llama-server" 2>/dev/null || true
    sleep 3
    echo ""
    
    echo -e "${GREEN}  commit $commit 測試完成${NC}"
    echo ""
done

# 恢復原始分支
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}  恢復原始分支: $ORIGINAL_BRANCH${NC}"
echo -e "${BLUE}============================================================${NC}"
git checkout "$ORIGINAL_BRANCH" 2>&1 | tail -5
echo ""

# 顯示最終 summary
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}  批量重測完成！${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo "  結果目錄: $RESULT_DIR"
echo "  Summary: $SUMMARY_FILE"
echo ""
cat "$SUMMARY_FILE"
echo ""

# 導出數據庫
echo -e "${BLUE}  導出數據庫為 CSV...${NC}"
python3 ./scripts/check/replay_bench_database.py export --output "$RESULT_DIR/database_export.csv" 2>/dev/null || true
echo ""

echo -e "${GREEN}  所有測試結果已保存到數據庫！${NC}"
echo ""
echo "  查看歷史記錄:"
echo "    python3 ./scripts/check/replay_bench_database.py list --limit 20"
echo ""
