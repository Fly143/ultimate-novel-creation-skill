#!/usr/bin/env bash
# ============================================================
# 多 Agent 技能同步脚本
# 把本仓库（agents-split 分支）同步到所有 novel 系 profile 的
# skills/ultimate-novel-creation-skill/ 副本。
#
# 原理：Hermes profile 隔离设计下，每个 agent 持有独立的技能副本。
#       本仓库是唯一事实源，本脚本负责一键分发。
#
# 用法：
#   bash sync_agents.sh                 # 同步到全部 novel 系 profile
#   bash sync_agents.sh novel-writer    # 只同步指定 profile
#   bash sync_agents.sh --dry-run       # 仅预览将同步哪些 profile
#
# 注意：目标目录会被整体覆盖（rm -rf + cp），确保目标 profile 存在。
# ============================================================

set -euo pipefail

# 仓库位置（本脚本所在目录）
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILES_ROOT="${LOCALAPPDATA:-$HOME/AppData/Local}/hermes/profiles"
SKILL_DIR_NAME="ultimate-novel-creation-skill"

# novel 系 profile 清单（主编 + 4 角色）
TARGETS=(novel novel-writer novel-auditor novel-fixer novel-setter)

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# 指定单个 profile 时只同步它
if [[ $# -gt 0 && "${1:-}" != "--dry-run" ]]; then
  TARGETS=("$1")
fi

echo "== 源仓库: $REPO_DIR"
echo "== 将同步到: ${TARGETS[*]}"
[[ $DRY_RUN == true ]] && echo "== [DRY-RUN] 仅预览，不执行 =="

for p in "${TARGETS[@]}"; do
  DST="$PROFILES_ROOT/$p/skills/$SKILL_DIR_NAME"
  if [[ ! -d "$PROFILES_ROOT/$p" ]]; then
    echo "!! 跳过 $p: profile 不存在 ($PROFILES_ROOT/$p)"
    continue
  fi
  if [[ $DRY_RUN == true ]]; then
    echo "   [预览] $p -> $DST"
    continue
  fi
  rm -rf "$DST"
  cp -r "$REPO_DIR" "$DST"
  ver=$(grep "^version:" "$DST/SKILL.md" 2>/dev/null | tr -d '\r' || echo "?")
  echo "   ✅ $p: $ver ($(ls "$DST/modules" | wc -l) 模块)"
done

[[ $DRY_RUN == true ]] && echo "== 预览结束（未做任何修改）=="
echo "完成。"
