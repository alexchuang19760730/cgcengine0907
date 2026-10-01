#!/bin/sh
# @@MARK@@ (commit-msg)
# Installed by: python3 scripts/check/doc_claim_gate.py --install-hook
# Rule (§76): every t/s in a commit message must point at an artifact that passed quote_gate.
#   The binding form is explicit: exactly one decimal t/s + exactly one .json path on the same line.
# Missing tool => warn and ALLOW (hooks/ is shared by every git worktree, so refusing here would
#   lock a sibling worktree out of committing). Set DOC_CLAIM_STRICT=1 to refuse instead.
@@CHAIN@@
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
tool="$root/scripts/check/doc_claim_gate.py"
if [ ! -f "$tool" ]; then
    echo "[doc_claim_gate] no $tool in this worktree => skipped (DOC_CLAIM_STRICT=1 to refuse)" >&2
    # 留痕（§78）：這一格是**放行**，不是「判過」——工具缺席時沒有任何一筆訊息被看過，而 stderr
    # 那一行只活在跑它的那個終端裡。寫一份戳記，讓 `--hooks-status` 事後看得見這次放行。
    printf '%s commit-msg %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$root" \
        > "$(dirname "$0")/doc_claim_gate.missing" 2>/dev/null
    [ "${DOC_CLAIM_STRICT:-0}" = "1" ] && exit 1
    exit 0
fi
exec python3 "$tool" --message-file "$1"
