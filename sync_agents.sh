#!/usr/bin/env bash
# ============================================================
# 多 Agent 技能同步脚本
# 把本仓库（agents-split 分支）同步到所有 novel 系 profile：
#   1. skills/ultimate-novel-creation-skill/ 整包覆盖
#   2. SOUL.md 从 agents/<角色>/system_prompt.md 重新生成
#
# 原理：Hermes profile 隔离设计下，每个 agent 持有独立的技能副本。
#       本仓库是唯一事实源，本脚本负责一键分发（技能 + SOUL）。
#
# 用法：
#   bash sync_agents.sh                 # 同步到全部 novel 系 profile
#   bash sync_agents.sh novel-writer    # 只同步指定 profile
#   bash sync_agents.sh --dry-run       # 仅预览将同步哪些 profile
#
# 注意：目标会被整体覆盖（rm -rf + cp），确保目标 profile 存在。
# ============================================================

set -euo pipefail

# 仓库位置（本脚本所在目录）
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILES_ROOT="${LOCALAPPDATA:-$HOME/AppData/Local}/hermes/profiles"
SKILL_DIR_NAME="ultimate-novel-creation-skill"

# profile -> 角色目录映射（角色目录决定 SOUL 来源）
declare -A ROLE_DIR=(
  [novel]="00_主编_director"
  [novel-writer]="01_写手_writer"
  [novel-auditor]="02_审核官_auditor"
  [novel-fixer]="03_修复师_fixer"
  [novel-setter]="04_设定师_setter"
)
declare -A ROLE_NAME=(
  [novel]="小说主编"
  [novel-writer]="小说写手"
  [novel-auditor]="小说审核官"
  [novel-fixer]="小说修复师"
  [novel-setter]="小说设定师"
)
TARGETS=("${!ROLE_DIR[@]}")

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true
[[ $# -gt 0 && "${1:-}" != "--dry-run" ]] && TARGETS=("$1")

echo "== 源仓库: $REPO_DIR"
echo "== 将同步: ${TARGETS[*]}（技能 + SOUL）"
[[ $DRY_RUN == true ]] && echo "== [DRY-RUN] 仅预览，不执行 =="

for p in "${TARGETS[@]}"; do
  DST="$PROFILES_ROOT/$p/skills/$SKILL_DIR_NAME"
  if [[ ! -d "$PROFILES_ROOT/$p" ]]; then
    echo "!! 跳过 $p: profile 不存在"
    continue
  fi
  if [[ $DRY_RUN == true ]]; then
    echo "   [预览] $p -> 技能覆盖 + SOUL 生成（源: agents/${ROLE_DIR[$p]}/system_prompt.md）"
    continue
  fi

  # 1) 技能整包覆盖
  rm -rf "$DST"
  cp -r "$REPO_DIR" "$DST"
  ver=$(grep "^version:" "$DST/SKILL.md" 2>/dev/null | tr -d '\r' || echo "?")

  # 2) SOUL 从 system_prompt.md 生成（去掉第三方宿主头部，换 Hermes Bot 头）
  SP="$REPO_DIR/agents/${ROLE_DIR[$p]}/system_prompt.md"
  if [[ -f "$SP" ]]; then
    python - "$SP" "$PROFILES_ROOT/$p/SOUL.md" "${ROLE_NAME[$p]}" <<'PYEOF'
import sys, os
def win(p):
    # MSYS 风格 /c/... -> Windows 原生 C:/...（python 不认 /c/ 前缀）
    if p.startswith('/c/'):
        return 'C:/' + p[3:]
    if p.startswith('/'):
        # 其他盘符（/d/ /e/ ...）也转换
        m = p[1:2]
        if m.isalpha() and len(p) > 2 and p[2] == '/':
            return m.upper() + ':/' + p[3:]
    return p
sp_path, soul_path, role_name = win(sys.argv[1]), win(sys.argv[2]), sys.argv[3]
content = open(sp_path, encoding="utf-8").read()
marker = "\n---\n"
idx = content.find(marker)
body = content[idx+len(marker):].strip() if idx != -1 else content.strip()
soul = (f"# {role_name}（Hermes Bot · 多Agent小说流水线）\n\n"
        f"> 本文件为该 Bot 的 SOUL.md，定义角色边界与职责。"
        f"配套技能：`ultimate-novel-creation-skill`（已随 profile 复制）。"
        f"协作方式：群聊中由主编按序 @ 呼叫，收到指令后按本 SOUL 执行，完成后交还主编。\n\n"
        f"{body}\n")
open(soul_path, "w", encoding="utf-8").write(soul)
PYEOF
    echo "   ✅ $p: $ver (${ROLE_DIR[$p]}/system_prompt.md -> SOUL.md)"
  else
    echo "   ⚠️ $p: 技能已同步 ($ver)，但 SOUL 源缺失: $SP（跳过 SOUL）"
  fi
done

[[ $DRY_RUN == true ]] && echo "== 预览结束（未做任何修改）=="
echo "完成。"
