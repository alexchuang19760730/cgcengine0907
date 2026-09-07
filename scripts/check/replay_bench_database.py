#!/usr/bin/env python3
"""
replay_bench_database.py — CGC Replay Benchmark 數據庫管理工具

功能:
  1. add: 將 replay 測試結果添加到數據庫
  2. list: 列出歷史記錄
  3. show: 顯示某條記錄的詳細信息
  4. compare: 比較兩個版本的指標
  5. trend: 生成趨勢報告
  6. baseline: 設置/查看 baseline 版本
  7. export: 導出數據為 CSV

數據庫結構:
  data/replay_bench/database.json — 主數據庫（所有記錄的索引）
  data/replay_bench/records/<commit>_<timestamp>.json — 每條記錄的完整數據

用法:
  python3 replay_bench_database.py add --input result.json --commit abc123 --branch dev
  python3 replay_bench_database.py list --limit 10
  python3 replay_bench_database.py show --commit abc123
  python3 replay_bench_database.py compare --baseline abc123 --current def456
  python3 replay_bench_database.py trend --metric decode_tps --profile coding
  python3 replay_bench_database.py baseline --set abc123 --name production
  python3 replay_bench_database.py export --output results.csv
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime


# 數據庫路徑
DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "replay_bench")
DB_FILE = os.path.join(DB_DIR, "database.json")
RECORDS_DIR = os.path.join(DB_DIR, "records")


def _ensure_db():
    """確保數據庫目錄和文件存在。"""
    os.makedirs(RECORDS_DIR, exist_ok=True)
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f:
            json.dump({"_version": "1.0", "records": [], "baselines": {}}, f, indent=2)


def _load_db():
    """加載數據庫。"""
    _ensure_db()
    with open(DB_FILE) as f:
        return json.load(f)


def _save_db(db):
    """保存數據庫。"""
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def _get_record_path(commit, timestamp):
    """獲取記錄文件路徑。"""
    ts = timestamp.replace(":", "-").replace(".", "-")
    return os.path.join(RECORDS_DIR, f"{commit[:8]}_{ts}.json")


def add_record(input_file, commit=None, branch=None, version=None, config=None, verdict=None, git_info=None):
    """將 replay 測試結果添加到數據庫。
    
    Args:
        input_file: replay_server_profile.py 的輸出 JSON 文件
        commit: git commit hash（可選，默認從 git 獲取）
        branch: git branch name（可選，默認從 git 獲取）
        version: 版本編號（可選，如 v1.0.0、release-2026-09-07）
        config: 配置信息字典（可選）
        verdict: 測試結論（pass/fail/neutral，可選）
        git_info: 額外的 git 信息字典（可選，包含 tag、message、author 等）
    
    Returns:
        record_id: 記錄 ID
    """
    _ensure_db()
    
    # 讀取輸入文件
    with open(input_file) as f:
        data = json.load(f)
    
    # 獲取完整的 git 信息
    git_info = git_info or {}
    
    # 獲取 commit
    if commit is None:
        commit = data.get("commit")  # 先從輸入文件獲取
        if not commit or commit == "unknown":
            try:
                commit = os.popen("git rev-parse HEAD").read().strip()
            except Exception:
                commit = "unknown"
    git_info["commit"] = commit
    git_info["commit_short"] = commit[:8] if commit and commit != "unknown" else "unknown"
    
    # 獲取 branch
    if branch is None:
        try:
            branch = os.popen("git rev-parse --abbrev-ref HEAD").read().strip()
        except Exception:
            branch = "unknown"
    git_info["branch"] = branch
    
    # 獲取版本編號（tag）
    if version is None:
        try:
            # 先嘗試獲取當前 HEAD 的 tag
            tag = os.popen("git describe --tags --exact-match 2>/dev/null").read().strip()
            if tag:
                version = tag
            else:
                # 嘗試獲取最近的 tag
                tag = os.popen("git describe --tags --abbrev=0 2>/dev/null").read().strip()
                if tag:
                    version = f"{tag}-dev"
        except Exception:
            pass
    if version is None:
        version = "untagged"
    git_info["version"] = version
    
    # 獲取更多 git 信息
    try:
        if "message" not in git_info:
            git_info["message"] = os.popen("git log -1 --pretty=%s").read().strip()
        if "author" not in git_info:
            git_info["author"] = os.popen("git log -1 --pretty=%an").read().strip()
        if "date" not in git_info:
            git_info["date"] = os.popen("git log -1 --pretty=%ci").read().strip()
    except Exception:
        pass
    
    timestamp = datetime.now().isoformat()
    
    # 提取關鍵指標
    record = {
        "commit": commit,
        "commit_short": git_info["commit_short"],
        "branch": branch,
        "version": version,
        "timestamp": timestamp,
        "git_info": git_info,
        "config": config or {},
        "verdict": verdict,
        "profiles": {},
        "three_factors": {},
        "system_state": {},
        "aggregate": {},
        "prefetch_drop_breakdown": {},
    }
    
    # 提取 prefetch drop breakdown（從頂級數據）
    pdb = data.get("prefetch_drop_breakdown")
    if pdb and pdb.get("available"):
        record["prefetch_drop_breakdown"] = {
            "total": pdb.get("total"),
            "no_free_slot": pdb.get("no_free_slot"),
            "dbuf_cap_skip": pdb.get("dbuf_cap_skip"),
            "drain_cleared": pdb.get("drain_cleared"),
            "zero_slot_fallback": pdb.get("zero_slot_fallback"),
            "lru_evicted_predicted": pdb.get("lru_evicted_predicted"),
            "bg_reassign_race": pdb.get("bg_reassign_race"),
            "guard_reject": pdb.get("guard_reject"),
            "dbuf2_scratch_invisible": pdb.get("dbuf2_scratch_invisible"),
            "one_shot_consumed": pdb.get("one_shot_consumed"),
            "fast_wait_expired": pdb.get("fast_wait_expired"),
            "trigger_too_late": pdb.get("trigger_too_late"),
            "collect_skipped": pdb.get("collect_skipped"),
            # Top 3 drop reasons (for quick analysis)
            "top_reasons": sorted(
                [(k, v) for k, v in pdb.items() if k.endswith("_pct") and v > 0],
                key=lambda x: x[1], reverse=True
            )[:3],
        }
    
    # 提取每個 profile 的指標
    if "profiles" in data:
        for profile_name, profile_data in data["profiles"].items():
            record["profiles"][profile_name] = {
                "quality": profile_data.get("quality", {}).get("score"),
                "decode_tps": profile_data.get("speed", {}).get("decode_tps"),
                "prefill_tps": profile_data.get("speed", {}).get("prefill_tps"),
                "draft_accept_pct": profile_data.get("speed", {}).get("draft_accept_pct"),
                "speed_vs_expected": profile_data.get("speed", {}).get("speed_vs_expected"),
                "peak_rss_mb": profile_data.get("memory", {}).get("peak_mb"),
            }
    elif "profile" in data:
        # 單個 profile 的結果
        profile_name = data["profile"]
        record["profiles"][profile_name] = {
            "quality": data.get("quality", {}).get("score"),
            "decode_tps": data.get("speed", {}).get("decode_tps"),
            "prefill_tps": data.get("speed", {}).get("prefill_tps"),
            "draft_accept_pct": data.get("speed", {}).get("draft_accept_pct"),
            "speed_vs_expected": data.get("speed", {}).get("speed_vs_expected"),
            "peak_rss_mb": data.get("memory", {}).get("peak_mb"),
        }
    
    # 提取三因子指標（從第一個有數據的 profile，或從頂級數據）
    def extract_three_factors(tf):
        if not tf or not tf.get("available"):
            return None
        f1 = tf.get("factor1_count_cold", {})
        f2 = tf.get("factor2_zero_slot", {})
        aux = tf.get("auxiliary", {})
        dp = tf.get("draft_prefetch", {})
        cold_rate = f1.get("cold_rate_all_pct")
        return {
            "count_cold_pct": cold_rate,
            "zero_slot_usage_pct": f2.get("zero_slot_usage_rate_pct"),
            "cache_hit_rate_pct": aux.get("cache_hit_rate_pct"),
            "expert_resident_hit_rate_pct": (100 - cold_rate) if cold_rate is not None else None,
            "draft_prefetch_hit_rate_pct": dp.get("draft_prefetch_hit_rate_pct"),
            "slot_utilization_pct": tf.get("factor3_memory_pressure", {}).get("slot_utilization_pct"),
        }
    
    # 先從頂級數據提取（單個 profile 的結果）
    tf_top = extract_three_factors(data.get("three_factors", {}))
    if tf_top:
        record["three_factors"] = tf_top
    else:
        # 從 profiles 中提取（--all-profiles 的結果）
        for profile_data in data.get("profiles", {}).values():
            tf = extract_three_factors(profile_data.get("three_factors", {}))
            if tf:
                record["three_factors"] = tf
                break
    
    # 提取機器狀態（從第一個有數據的 profile，或從頂級數據）
    def extract_system_state(ss):
        if not ss:
            return None
        after = ss.get("after", {})
        return {
            "overall_tier": ss.get("overall_tier"),
            "memory_free_pct": after.get("memory", {}).get("free_pct"),
            "cpu_idle_pct": after.get("cpu", {}).get("idle_pct"),
            "swap_used_mb": after.get("memory", {}).get("swap_used_mb"),
            "expected_decode_tps": ss.get("expected_decode_tps"),
        }
    
    # 先從頂級數據提取（單個 profile 的結果）
    ss_top = extract_system_state(data.get("system_state", {}))
    if ss_top:
        record["system_state"] = ss_top
    else:
        # 從 profiles 中提取（--all-profiles 的結果）
        for profile_data in data.get("profiles", {}).values():
            ss = extract_system_state(profile_data.get("system_state", {}))
            if ss:
                record["system_state"] = ss
                break
    
    # 計算聚合指標
    decode_tps_list = [p["decode_tps"] for p in record["profiles"].values() if p["decode_tps"] is not None]
    quality_list = [p["quality"] for p in record["profiles"].values() if p["quality"] is not None]
    if decode_tps_list:
        record["aggregate"] = {
            "avg_decode_tps": round(sum(decode_tps_list) / len(decode_tps_list), 2),
            "median_decode_tps": sorted(decode_tps_list)[len(decode_tps_list) // 2],
            "min_decode_tps": min(decode_tps_list),
            "max_decode_tps": max(decode_tps_list),
        }
    if quality_list:
        record["aggregate"]["min_quality"] = min(quality_list)
        record["aggregate"]["avg_quality"] = round(sum(quality_list) / len(quality_list), 3)
    
    # 保存完整記錄到單獨文件
    record_path = _get_record_path(commit, timestamp)
    with open(record_path, "w") as f:
        json.dump({"record": record, "raw": data}, f, indent=2, ensure_ascii=False)
    
    # 更新數據庫索引
    db = _load_db()
    db["records"].append({
        "commit": commit,
        "commit_short": git_info["commit_short"],
        "branch": branch,
        "version": version,
        "timestamp": timestamp,
        "record_path": os.path.relpath(record_path, DB_DIR),
        "summary": {
            "avg_decode_tps": record["aggregate"].get("avg_decode_tps"),
            "min_quality": record["aggregate"].get("min_quality"),
            "count_cold_pct": record["three_factors"].get("count_cold_pct"),
            "system_tier": record["system_state"].get("overall_tier"),
            "verdict": verdict,
        }
    })
    _save_db(db)
    
    print(f"[database] 記錄已添加:")
    print(f"  commit: {commit[:8]} ({branch} / {version})")
    print(f"  timestamp: {timestamp}")
    print(f"  avg_decode_tps: {record['aggregate'].get('avg_decode_tps')}")
    print(f"  min_quality: {record['aggregate'].get('min_quality')}")
    print(f"  count_cold_pct: {record['three_factors'].get('count_cold_pct')}")
    print(f"  system_tier: {record['system_state'].get('overall_tier')}")
    print(f"  verdict: {verdict}")
    
    return commit


def list_records(limit=10, branch=None, version=None):
    """列出歷史記錄。"""
    db = _load_db()
    records = db.get("records", [])
    
    if branch:
        records = [r for r in records if r.get("branch") == branch]
    if version:
        records = [r for r in records if r.get("version") == version]
    
    records = sorted(records, key=lambda r: r["timestamp"], reverse=True)[:limit]
    
    print(f"{'Commit':<10} {'Version':<15} {'Branch':<12} {'Timestamp':<16} {'Avg TPS':<9} {'Qual':<7} {'Cold%':<7} {'Drop':<6} {'Tier':<5} {'Verdict':<8}")
    print("-" * 110)
    for r in records:
        s = r.get("summary", {})
        pdb = r.get("prefetch_drop_breakdown", {})
        drop_total = pdb.get("total", "?") if pdb else "?"
        print(f"{r.get('commit_short', r['commit'][:8]):<10} "
              f"{str(r.get('version', '?'))[:14]:<15} "
              f"{r.get('branch', '?')[:11]:<12} "
              f"{r['timestamp'][:16]:<16} "
              f"{str(s.get('avg_decode_tps', '?')):<9} "
              f"{str(s.get('min_quality', '?')):<7} "
              f"{str(s.get('count_cold_pct', '?')):<7} "
              f"{str(drop_total):<6} "
              f"{str(s.get('system_tier', '?')):<5} "
              f"{str(s.get('verdict', '?')):<8}")


def show_record(commit):
    """顯示某條記錄的詳細信息。"""
    db = _load_db()
    records = db.get("records", [])
    
    # 找到匹配的記錄（支持部分 commit hash）
    matching = [r for r in records if r["commit"].startswith(commit)]
    if not matching:
        print(f"錯誤: 找不到 commit {commit}")
        return
    
    # 取最新的一條
    record_info = sorted(matching, key=lambda r: r["timestamp"], reverse=True)[0]
    record_path = os.path.join(DB_DIR, record_info["record_path"])
    
    with open(record_path) as f:
        data = json.load(f)
    
    record = data["record"]
    print(f"=== 記錄詳情: {record['commit'][:8]} @ {record['timestamp']} ===")
    print(f"Branch: {record['branch']}")
    print(f"Verdict: {record.get('verdict')}")
    print()
    
    print("【各 Profile 指標】")
    for profile_name, profile_data in record["profiles"].items():
        print(f"  {profile_name}:")
        print(f"    quality: {profile_data['quality']}")
        print(f"    decode_tps: {profile_data['decode_tps']}")
        print(f"    prefill_tps: {profile_data['prefill_tps']}")
        print(f"    draft_accept: {profile_data['draft_accept_pct']}%")
        print(f"    peak_rss: {profile_data['peak_rss_mb']} MB")
    print()
    
    print("【三因子指標】")
    for k, v in record["three_factors"].items():
        print(f"  {k}: {v}")
    print()
    
    print("【機器狀態】")
    for k, v in record["system_state"].items():
        print(f"  {k}: {v}")
    print()
    
    print("【聚合指標】")
    for k, v in record["aggregate"].items():
        print(f"  {k}: {v}")


def compare_records(baseline_commit, current_commit):
    """比較兩個版本的指標。"""
    db = _load_db()
    records = db.get("records", [])
    
    def find_record(commit_prefix):
        matching = [r for r in records if r["commit"].startswith(commit_prefix)]
        if not matching:
            return None
        record_info = sorted(matching, key=lambda r: r["timestamp"], reverse=True)[0]
        record_path = os.path.join(DB_DIR, record_info["record_path"])
        with open(record_path) as f:
            return json.load(f)["record"]
    
    baseline = find_record(baseline_commit)
    current = find_record(current_commit)
    
    if not baseline or not current:
        print("錯誤: 找不到指定的 commit")
        return
    
    print(f"=== 版本對比: {baseline_commit[:8]} (baseline) vs {current_commit[:8]} (current) ===")
    print()
    
    # 比較各 profile
    print("【各 Profile 指標對比】")
    print(f"{'Profile':<15} {'指標':<20} {'Baseline':<12} {'Current':<12} {'變化':<12} {'狀態':<10}")
    print("-" * 85)
    
    for profile_name in ["qa-zh", "longform-zh", "coding"]:
        b = baseline["profiles"].get(profile_name, {})
        c = current["profiles"].get(profile_name, {})
        for metric in ["quality", "decode_tps", "prefill_tps", "draft_accept_pct"]:
            bv = b.get(metric)
            cv = c.get(metric)
            if bv is not None and cv is not None:
                delta = cv - bv
                delta_pct = (delta / bv * 100) if bv != 0 else 0
                if metric == "quality":
                    status = "✅ 改善" if delta > 0 else ("❌ 退化" if delta < -0.05 else "➖ 持平")
                else:
                    status = "✅ 改善" if delta_pct > 2 else ("❌ 退化" if delta_pct < -2 else "➖ 持平")
                print(f"{profile_name:<15} {metric:<20} {bv:<12.3f} {cv:<12.3f} {delta:+.2f} ({delta_pct:+.1f}%) {status:<10}")
    
    print()
    
    # 比較三因子
    print("【三因子指標對比】")
    for metric in ["count_cold_pct", "zero_slot_usage_pct", "cache_hit_rate_pct", "expert_resident_hit_rate_pct"]:
        bv = baseline["three_factors"].get(metric)
        cv = current["three_factors"].get(metric)
        if bv is not None and cv is not None:
            delta = cv - bv
            # count_cold 和 zero_slot 越低越好
            if metric in ["count_cold_pct", "zero_slot_usage_pct"]:
                status = "✅ 改善" if delta < -1 else ("❌ 退化" if delta > 1 else "➖ 持平")
            else:
                status = "✅ 改善" if delta > 1 else ("❌ 退化" if delta < -1 else "➖ 持平")
            print(f"  {metric:<35} {bv:<10.2f}% {cv:<10.2f}% {delta:+.2f}% {status}")


def set_baseline(commit, name="production"):
    """設置 baseline 版本。"""
    db = _load_db()
    if "baselines" not in db:
        db["baselines"] = {}
    db["baselines"][name] = commit
    _save_db(db)
    print(f"[database] Baseline '{name}' 已設置為: {commit}")


def export_csv(output_file):
    """導出數據為 CSV。"""
    db = _load_db()
    records = db.get("records", [])
    
    import csv
    with open(output_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "commit", "branch", "timestamp", "verdict",
            "qa_quality", "qa_decode_tps", "qa_prefill_tps",
            "longform_quality", "longform_decode_tps", "longform_prefill_tps",
            "coding_quality", "coding_decode_tps", "coding_prefill_tps",
            "count_cold_pct", "zero_slot_usage_pct", "cache_hit_rate_pct",
            "system_tier", "memory_free_pct", "cpu_idle_pct",
            "avg_decode_tps", "min_quality"
        ])
        
        for r in records:
            record_path = os.path.join(DB_DIR, r["record_path"])
            with open(record_path) as rf:
                record = json.load(rf)["record"]
            
            row = [
                record["commit"][:8],
                record["branch"],
                record["timestamp"],
                record.get("verdict", ""),
            ]
            
            for profile in ["qa-zh", "longform-zh", "coding"]:
                p = record["profiles"].get(profile, {})
                row.extend([p.get("quality"), p.get("decode_tps"), p.get("prefill_tps")])
            
            tf = record["three_factors"]
            row.extend([tf.get("count_cold_pct"), tf.get("zero_slot_usage_pct"), tf.get("cache_hit_rate_pct")])
            
            ss = record["system_state"]
            row.extend([ss.get("overall_tier"), ss.get("memory_free_pct"), ss.get("cpu_idle_pct")])
            
            agg = record["aggregate"]
            row.extend([agg.get("avg_decode_tps"), agg.get("min_quality")])
            
            writer.writerow(row)
    
    print(f"[database] 數據已導出到: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="CGC Replay Benchmark 數據庫管理工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # add
    add_parser = subparsers.add_parser("add", help="添加記錄到數據庫")
    add_parser.add_argument("--input", required=True, help="replay 輸出 JSON 文件")
    add_parser.add_argument("--commit", help="git commit hash")
    add_parser.add_argument("--branch", help="git branch name")
    add_parser.add_argument("--version", help="版本編號 (如 v1.0.0, release-2026-09-07)")
    add_parser.add_argument("--verdict", choices=["pass", "fail", "neutral"], help="測試結論")
    
    # list
    list_parser = subparsers.add_parser("list", help="列出歷史記錄")
    list_parser.add_argument("--limit", type=int, default=10, help="顯示條數")
    list_parser.add_argument("--branch", help="按分支過濾")
    list_parser.add_argument("--version", help="按版本過濾")
    
    # show
    show_parser = subparsers.add_parser("show", help="顯示記錄詳情")
    show_parser.add_argument("--commit", required=True, help="commit hash（支持部分匹配）")
    
    # compare
    compare_parser = subparsers.add_parser("compare", help="比較兩個版本")
    compare_parser.add_argument("--baseline", required=True, help="baseline commit")
    compare_parser.add_argument("--current", required=True, help="current commit")
    
    # baseline
    baseline_parser = subparsers.add_parser("baseline", help="設置/查看 baseline")
    baseline_parser.add_argument("--set", help="設置 baseline commit")
    baseline_parser.add_argument("--name", default="production", help="baseline 名稱")
    
    # export
    export_parser = subparsers.add_parser("export", help="導出為 CSV")
    export_parser.add_argument("--output", default="replay_bench_export.csv", help="輸出文件")
    
    args = parser.parse_args()
    
    if args.command == "add":
        add_record(args.input, args.commit, args.branch, version=args.version, verdict=args.verdict)
    elif args.command == "list":
        list_records(args.limit, args.branch, args.version)
    elif args.command == "show":
        show_record(args.commit)
    elif args.command == "compare":
        compare_records(args.baseline, args.current)
    elif args.command == "baseline":
        if args.set:
            set_baseline(args.set, args.name)
        else:
            db = _load_db()
            print("當前 baselines:")
            for name, commit in db.get("baselines", {}).items():
                print(f"  {name}: {commit}")
    elif args.command == "export":
        export_csv(args.output)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
