#!/bin/sh
# @@MARK@@ (pre-push)
# Installed by: python3 scripts/check/doc_claim_gate.py --install-hook
# Range = exactly what this push adds: remote_sha..local_sha, or "<local_sha> --not --remotes"
# for a brand-new branch. Commits already on a remote are not re-scanned: the message's range IS
# its ledger (§76). Same missing-tool rule as commit-msg.
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
tool="$root/scripts/check/doc_claim_gate.py"
if [ ! -f "$tool" ]; then
    echo "[doc_claim_gate] no $tool in this worktree => pre-push scan skipped (DOC_CLAIM_STRICT=1 to refuse)" >&2
    # 留痕（§78）：同 commit-msg，但這一支更危險 —— 它擋的是**已經在本機成形的歷史**。
    printf '%s pre-push %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$root" \
        > "$(dirname "$0")/doc_claim_gate.missing" 2>/dev/null
    [ "${DOC_CLAIM_STRICT:-0}" = "1" ] && exit 1
    exit 0
fi
zero=0000000000000000000000000000000000000000
stamp="$(dirname "$0")/doc_claim_gate.since"
rc=0
while read -r _local_ref local_sha _remote_ref remote_sha; do
    [ "$local_sha" = "$zero" ] && continue
    if [ "$remote_sha" = "$zero" ]; then
        range="$local_sha --not --remotes"
    else
        range="$remote_sha..$local_sha"
    fi
    if [ -f "$stamp" ]; then
        python3 "$tool" --commits "$range" --commit-since "$(cat "$stamp")" || rc=1
    else
        python3 "$tool" --commits "$range" || rc=1
    fi
done
exit $rc
