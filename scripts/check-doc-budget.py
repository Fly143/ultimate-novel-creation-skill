#!/usr/bin/env python3
"""热路径文档预算守卫（与 scripts/check-doc-budget.ps1 同口径）。

规则单源 = scripts/doc-budget.json；度量 = 去掉所有空白后的字符数。
- 文件条目支持 `路径#小节标题`（只计该小节，到下个同级/更高级标题为止；代码围栏里的 # 不算）。
- paths[].branches 互斥取最大；paths[].stacking 叠加取全和；path_ceiling.exempt 内 label 不判上限。
- generated 条目由脚本产出，不手测（只报登记状态）。

用法：
  python scripts/check-doc-budget.py                  # 校验（超预算 exit1）
  python scripts/check-doc-budget.py --print-baseline  # 打印基线实测表

退出码：0=文件与路径全部在预算内；1=超预算/缺文件/规则缺失。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BUDGET_PATH = Path(__file__).resolve().parent / "doc-budget.json"
# 度量口径（与 ps1 逐字一致）：UTF-8 字节解码剥 BOM → 换行归一化为 \n → 移除空白集合
WS_RE = re.compile(r"[ \t\n\f\v\u00a0\u2028\u2029\u3000]+")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def read_raw(root: Path, rel: str) -> str:
    text = (root / rel).read_bytes().decode("utf-8-sig", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def load_rules() -> dict:
    with BUDGET_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def strip_ws(text: str) -> int:
    return len(WS_RE.sub("", text))


def read_text(root: Path, rel: str) -> str:
    """归一化读取（与 ps1 同口径：字节解码+剥BOM+换行归一化）。"""
    return read_raw(root, rel)


def section_slice(text: str, title: str) -> str | None:
    """返回 `# 标题` 小节正文（到下个同级/更高级标题）；找不到返回 None。"""
    lines = text.splitlines()
    in_fence = False
    start = None
    level = 0
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEAD_RE.match(line)
        if not m:
            continue
        if start is None:
            if title in m.group(2):
                start = i
                level = len(m.group(1))
            continue
        if len(m.group(1)) <= level:
            return "\n".join(lines[start:i])
    if start is None:
        return None
    return "\n".join(lines[start:])


def measure(root: Path, ref: str) -> tuple[int | None, str]:
    """ref = 'path' 或 'path#标题' → (字符数, 状态)。"""
    rel, _, frag = ref.partition("#")
    p = root / rel
    if not p.is_file():
        return None, f"缺文件 {rel}"
    text = read_text(root, rel)
    if frag:
        sliced = section_slice(text, frag)
        if sliced is None:
            return None, f"缺小节 {rel}#{frag}"
        return strip_ws(sliced), f"{rel}#{frag}"
    return strip_ws(text), rel


def sum_refs(root: Path, refs: list[str]) -> tuple[int, list[str]]:
    total = 0
    problems: list[str] = []
    for ref in refs:
        n, status = measure(root, ref)
        if n is None:
            problems.append(status)
        else:
            total += n
    return total, problems


def main() -> int:
    ap = argparse.ArgumentParser(description="热路径文档预算守卫")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]), help="仓库根")
    ap.add_argument("--print-baseline", action="store_true", help="额外打印基线汇总")
    args = ap.parse_args()
    root = Path(args.root).expanduser().resolve()
    if not BUDGET_PATH.is_file():
        print(f"❌ 缺少规则单源：{BUDGET_PATH}")
        return 1
    rules = load_rules()
    errors: list[str] = []

    print(f"【check-doc-budget】root={root}")
    print(f"规则={BUDGET_PATH.name} 度量=去空白字符")

    # 文件级
    print("\n文件预算：")
    measured: dict[str, int] = {}
    for item in rules.get("files", []):
        ref, budget = item["path"], int(item["budget"])
        n, status = measure(root, ref)
        if n is None:
            errors.append(f"文件校验：{status}")
            print(f"  ❌ {ref} — {status}")
            continue
        measured[ref] = n
        if n > budget:
            errors.append(f"超预算：{ref} {n:,} > {budget:,}（{item.get('why','')}）")
            print(f"  ❌ {ref} {n:,}/{budget:,} 超出 {n - budget:,} 字符")
        else:
            print(f"  ✅ {ref} {n:,}/{budget:,}（余 {budget - n:,}）")

    # 生成型条目
    for g in rules.get("generated", []):
        print(f"  ⚙ generated（脚本产出，不手测）：{g if isinstance(g, str) else json.dumps(g, ensure_ascii=False)}")

    # 路径级
    ceiling = rules.get("path_ceiling", {}) or {}
    limit = int(ceiling.get("limit", 0))
    exempt = set(ceiling.get("exempt", []) or [])
    print(f"\n路径合计（上限 path_ceiling.limit={limit:,}）：")
    worst = 0
    worst_label = ""
    for path in rules.get("paths", []):
        label = path["label"]
        total, problems = sum_refs(root, list(path.get("files", [])))
        detail = [f"base={total:,}"]
        for ref in path.get("branches", []) or []:
            # branches = 多组互斥分支，每组是一串 ref
            branch_totals = []
            for grp in ref:
                b, probs = sum_refs(root, list(grp))
                errors.extend(f"{label} 分支：{p}" for p in probs)
                branch_totals.append(b)
            if branch_totals:
                total += max(branch_totals)
                detail.append(f"+branch(max)={max(branch_totals):,}")
        for grp in path.get("stacking", []) or []:
            s, probs = sum_refs(root, list(grp))
            errors.extend(f"{label} 叠加：{p}" for p in probs)
            total += s
            detail.append(f"+stack={s:,}")
        errors.extend(f"{label}：{p}" for p in problems)
        flag = "✅"
        if label in exempt:
            flag = "⚪ exempt"
        elif limit and total > limit:
            errors.append(f"路径超上限：{label} {total:,} > {limit:,}")
            flag = "❌"
        if label not in exempt:
            if total > worst:
                worst, worst_label = total, label
        print(f"  {flag} {label} {total:,}/{limit:,}（{' '.join(detail)}）")

    print()
    if args.print_baseline:
        print(f"基线：最坏路径 {worst:,}（{worst_label}）/ 上限 {limit:,}")
    if errors:
        print(f"❌ 预算未通过，{len(errors)} 项：")
        for e in errors:
            print(f"  - {e}")
        print("处置：删等量旧文本，或显式调高 doc-budget.json 预算并写明 why（禁止静默放宽）")
        return 1
    print(f"✅ 全部在预算内（最坏路径 {worst:,} ≤ {limit:,}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
