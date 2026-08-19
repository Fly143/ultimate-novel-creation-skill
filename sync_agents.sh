#!/usr/bin/env bash
# ============================================================
# 多 Agent 技能同步脚本（自动识别实例名版）
#
# 用途：把本仓库（agents-split 分支）同步到所有 Hermes profile，
#   1. skills/ultimate-novel-creation-skill/ 整包覆盖
#   2. SOUL.md 从 agents/<角色>/system_prompt.md 重新生成
#
# 关键改进：自动识别 profile（实例）名 → 角色目录映射，
#   不再硬编码 novel / novel-writer 等固定名。
#
# 识别逻辑（按 profile 目录名自动判断角色）：
#   director / novel / novel-director / main / 主编 / 主编*
#       → 00_主编_director
#   writer / novel-writer / 写手 / 写手*
#       → 01_写手_writer
#   auditor / novel-auditor / 审核 / 审核官 / 审核官*
#       → 02_审核官_auditor
#   fixer / novel-fixer / 修复 / 修复师 / 修复师*
#       → 03_修复师_fixer
#   setter / novel-setter / 设定 / 设定师 / 设定师*
#       → 04_设定师_setter
#   未被识别的 profile 会被跳过（除非用 -f 强制指定映射）。
#
# 用法：
#   bash sync_agents.sh                 # 同步到全部已识别的 profile
#   bash sync_agents.sh --dry-run       # 仅预览
#   bash sync_agents.sh <profile名>     # 只同步指定 profile（须存在于 PROFILES_ROOT）
#   bash sync_agents.sh <profile名>=<角色目录名>   # 强制映射，例如 writer=01_写手_writer
#
# 注意：目标会被整体覆盖（rm -rf + cp），确保目标 profile 存在。
# ============================================================

set -euo pipefail

# 仓库位置：优先用环境变量 UNCS_REPO，否则用脚本所在目录
# 用 pwd -W 取 MSYS 原生 Windows 路径，避免 /tmp/ 这类挂载点被 python 误转换
if [[ -n "${UNCS_REPO:-}" ]]; then
  REPO_DIR="$UNCS_REPO"
else
  cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null || true
  REPO_DIR="$(pwd -W 2>/dev/null || pwd)"
fi
PROFILES_ROOT="${LOCALAPPDATA:-$HOME/AppData/Local}/hermes/profiles"
SKILL_DIR_NAME="ultimate-novel-creation-skill"

# ---- 角色目录 → 角色中文名 ----
declare -A ROLE_NAME=(
  [00_主编_director]="小说主编"
  [01_写手_writer]="小说写手"
  [02_审核官_auditor]="小说审核官"
  [03_修复师_fixer]="小说修复师"
  [04_设定师_setter]="小说设定师"
)

# ---- profile 名 → 角色目录（自动识别映射）----
# 每个 profile 名（小写）匹配任一关键字即命中；最长匹配优先。
declare -A FORCE_MAP=()   # 来自命令行 <profile>=<role>
DRY_RUN=false
SINGLE_TARGET=""

for arg in "$@"; do
  if [[ "$arg" == "--dry-run" ]]; then DRY_RUN=true; continue; fi
  if [[ "$arg" == *"="* ]]; then
    p="${arg%%=*}"; r="${arg#*=}"
    FORCE_MAP["$p"]="$r"
  else
    SINGLE_TARGET="$arg"
  fi
done

resolve_role() {
  # $1 = profile 目录名
  local pn
  pn="$(echo "$1" | tr '[:upper:]' '[:lower:]')"
  # 强制映射优先
  if [[ -n "${FORCE_MAP[$1]+x}" ]]; then echo "${FORCE_MAP[$1]}"; return; fi
  case "$pn" in
    *director*|*novel*|*main*|*主编*) echo "00_主编_director";;
    *writer*|*写手*)                  echo "01_写手_writer";;
    *auditor*|*审核*)                 echo "02_审核官_auditor";;
    *fixer*|*修复*)                   echo "03_修复师_fixer";;
    *setter*|*设定*)                  echo "04_设定师_setter";;
    *)                               echo "";;   # 未识别
  esac
}

echo "== 源仓库: $REPO_DIR"
echo "== profile 根: $PROFILES_ROOT"

if [[ -n "$SINGLE_TARGET" ]]; then
  TARGETS=("$SINGLE_TARGET")
  echo "== 仅同步指定: $SINGLE_TARGET"
else
  # 列出 profiles 下所有子目录
  if [[ ! -d "$PROFILES_ROOT" ]]; then
    echo "!! profile 根不存在: $PROFILES_ROOT"
    exit 1
  fi
  shopt -s nullglob
  TARGETS=("$PROFILES_ROOT"/*/)
  TARGETS=("${TARGETS[@]%/}")   # 去掉尾部 /
  TARGETS=("${TARGETS[@]##*/}") # 只留目录名
  echo "== 发现 profile: ${TARGETS[*]}"
fi

[[ $DRY_RUN == true ]] && echo "== [DRY-RUN] 仅预览，不执行 =="

for p in "${TARGETS[@]}"; do
  DST="$PROFILES_ROOT/$p/skills/$SKILL_DIR_NAME"
  if [[ ! -d "$PROFILES_ROOT/$p" ]]; then
    echo "!! 跳过 $p: profile 不存在"
    continue
  fi

  role="$(resolve_role "$p")"
  if [[ -z "$role" ]]; then
    echo "!! 跳过 $p: 无法自动识别角色（可用 writer=01_写手_writer 强制映射）"
    continue
  fi
  if [[ ! -d "$REPO_DIR/agents/$role" ]]; then
    echo "!! 跳过 $p: 角色目录缺失 agents/$role"
    continue
  fi

  if [[ $DRY_RUN == true ]]; then
    echo "   [预览] $p -> $role (技能覆盖 + SOUL 生成)"
    continue
  fi

  # 1) 技能整包覆盖
  rm -rf "$DST"
  cp -r "$REPO_DIR" "$DST"
  rm -rf "$DST/.git"
  ver=$(grep "^version:" "$DST/SKILL.md" 2>/dev/null | tr -d '\r' || echo "?")

  # 2) SOUL 从 system_prompt.md 生成（换成 Hermes Bot 头）
  SP="$REPO_DIR/agents/$role/system_prompt.md"
  if [[ -f "$SP" ]]; then
    python - "$SP" "$PROFILES_ROOT/$p/SOUL.md" "${ROLE_NAME[$role]}" <<'PYEOF'
import sys
def win(p):
    if p.startswith('/c/'):
        return 'C:/' + p[3:]
    if p.startswith('/'):
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
    echo "   ✅ $p: $ver (agents/$role/system_prompt.md -> SOUL.md)"
  else
    echo "   ⚠️ $p: 技能已同步 ($ver)，但 SOUL 源缺失: $SP（跳过 SOUL）"
  fi
done

[[ $DRY_RUN == true ]] && echo "== 预览结束（未做任何修改）=="
echo "完成。"
