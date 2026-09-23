#!/usr/bin/env python3
"""章节 .done 客观核验（与 scripts/check-done.ps1 同口径）。

【同步约定】规则源 = scripts/check-done-rules.json（单源）。
改规则只改 JSON，并跑 `python .github/scripts/test-check-done.py` 确认 py+ps1 双入口一致。

标准：
- 闭环只认 `[书名]/.done/` 空标记（size=0）；非空无效。
- 标记/报告章号：`第NNN章` 与 `第N章`。
- 默认硬校验（有 zhuque 时）：报告须含「人工证据包」；结构证据类别 ≥2 且至少 1 类强证据；
  报告声明的段号不得超过正文段落数（能找到正文时）。`--soft-report` 降为警告。
- 默认要求 `第NNN章_chapter.done`；`--no-chapter-done` 仅供流水线中途检查。
- `.done_zhuque` = 4.1b–4.1e 已登记；朱雀达标以用户回传三态为准。

用法：
  python scripts/check-done.py --project "路径/书名" --chapter 12
  python scripts/check-done.py -p . -c 10 --no-chapter-done
  python scripts/check-done.py -p . -c 12 --soft-report
  python scripts/check-done.py -p . -c 12 --allow-non-empty

退出码：0=必需空标记齐且默认硬校验通过；1=缺标记/非空/路径错误/硬校验失败。
`--allow-non-empty`：仅限用户调试；宿主/LLM 禁止携带。
`--strict-report`：兼容别名（默认已硬校验，等价于不加）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parent / "check-done-rules.json"


def load_rules() -> dict:
    with RULES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


RULES = load_rules()
BASE_MARKS: list[str] = list(RULES["base_marks"])
EVIDENCE_RULES: list[tuple[str, str, bool]] = [
    (r["name"], r["pattern"], bool(r["strong"])) for r in RULES["evidence_rules"]
]
SECTION_TITLE: str = RULES["section_title"]
MIN_HITS: int = int(RULES["min_hits"])
REQUIRE_STRONG: bool = bool(RULES["require_strong"])
CLAIM_PARA_RE: re.Pattern[str] = re.compile(RULES["claim_para_pattern"])
BODY_GLOBS: list[str] = list(RULES["body_globs"])


def required_marks(chapter: int) -> list[str]:
    marks = list(BASE_MARKS)
    if chapter % 10 == 0:
        marks.append("innovation")
    return marks


def classify_marker(path: Path) -> str:
    """返回 present / nonempty / missing。"""
    if not path.exists():
        return "missing"
    try:
        if path.is_file() and path.stat().st_size == 0:
            return "present"
        return "nonempty"
    except OSError:
        return "nonempty"


def _candidate_names(nnn: str, chapter: int, suffix: str) -> list[str]:
    """章号文件名：优先零填充 NNN，同时兼容不填充 N。"""
    names = [f"第{nnn}章{suffix}"]
    unpadded = f"第{chapter}章{suffix}"
    if unpadded not in names:
        names.append(unpadded)
    return names


def resolve_existing(
    base_dir: Path, nnn: str, chapter: int, suffix: str
) -> tuple[Path | None, list[str]]:
    """返回 (首个命中路径, 冲突告警列表)。"""
    found: list[Path] = []
    for name in _candidate_names(nnn, chapter, suffix):
        path = base_dir / name
        if path.exists():
            found.append(path)
    warnings: list[str] = []
    if len(found) > 1:
        names = ", ".join(p.name for p in found)
        warnings.append(f"章号命名冲突：同时存在 {names}（默认优先零填充）→ {base_dir}")
    return (found[0] if found else None), warnings


def classify_first(
    base_dir: Path, nnn: str, chapter: int, suffix: str
) -> tuple[str, list[str]]:
    found, warnings = resolve_existing(base_dir, nnn, chapter, suffix)
    if found is None:
        return "missing", warnings
    return classify_marker(found), warnings


def match_evidence(text: str) -> tuple[list[str], list[str], list[str]]:
    """返回 (命中类别, 强证据类别, 强类别全表)。"""
    hits: list[str] = []
    strong_hits: list[str] = []
    strong_names: list[str] = []
    for name, pattern, is_strong in EVIDENCE_RULES:
        if is_strong:
            strong_names.append(name)
        if re.search(pattern, text):
            hits.append(name)
            if is_strong:
                strong_hits.append(name)
    return hits, strong_hits, strong_names


def count_body_paragraphs(text: str) -> int:
    """按空行分段计数；无空行则按非空行。首行章节标题不计入段号口径时跳过。"""
    blocks: list[str] = []
    cur: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            continue
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    if not blocks:
        return 0
    # 约定：第1块多为章节标题，段号从正文块起算时去掉标题
    body_blocks = blocks[1:] if len(blocks) > 1 else blocks
    if len(blocks) > 1 and any(x in blocks[0][:40] for x in ("第", "章")):
        return len(body_blocks)
    # 无明显标题：若全部是短行且无空行结构，按非空行
    if len(blocks) == 1 and "\n" in blocks[0]:
        lines = [ln for ln in blocks[0].splitlines() if ln.strip()]
        if len(lines) > 1:
            # 单块内换行：视作逐行段落
            return max(0, len(lines) - 1) if lines and lines[0][:2].startswith("第") else len(lines)
    return len(body_blocks)


def find_body(project: Path, nnn: str, chapter: int) -> Path | None:
    body_dir = project / "正文"
    if not body_dir.is_dir():
        return None
    for glob in BODY_GLOBS:
        pattern = glob.format(nnn=nnn, ch=chapter)
        # fnmatch on relative path with /
        for p in body_dir.parent.glob(pattern.replace("\\", "/")):
            if p.is_file():
                return p
        for p in body_dir.glob(Path(pattern).name):
            if p.is_file():
                return p
    # 兜底：前缀匹配
    for prefix in (f"第{nnn}章", f"第{chapter}章"):
        matches = sorted(body_dir.glob(f"{prefix}*.txt"))
        if matches:
            return matches[0]
    return None


def check_body_claims(report_text: str, body: Path) -> list[str]:
    """报告声明段号 vs 正文段落数；返回错误消息列表。"""
    claimed = [int(m) for m in CLAIM_PARA_RE.findall(report_text)]
    if not claimed:
        return []
    try:
        body_text = body.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"正文交叉校验：无法读取正文 {body}：{exc}"]
    para_n = count_body_paragraphs(body_text)
    max_claim = max(claimed)
    if para_n <= 0:
        return []
    if max_claim > para_n:
        return [
            f"正文交叉校验：报告声明最大段号 第{max_claim}段，但正文仅约 {para_n} 段"
            f"（报告段号必须落在正文范围内）→ {body}"
        ]
    return []


def check_report(
    project: Path, nnn: str, chapter: int, has_zhuque: bool
) -> tuple[str, list[str], list[str]]:
    """返回 (状态, 错误/警告消息列表, 章号冲突告警)。

    状态：skipped / missing / no-section / weak-section / body-mismatch / ok
    """
    if not has_zhuque:
        return "skipped", [], []
    report_dir = project / "报告"
    report_path, report_warns = resolve_existing(
        report_dir, nnn, chapter, "_全量审核报告.md"
    )
    if report_path is None or not report_path.is_file():
        expected = " / ".join(
            str(report_dir / n)
            for n in _candidate_names(nnn, chapter, "_全量审核报告.md")
        )
        return (
            "missing",
            [
                f"报告校验：存在 .done_zhuque，但未找到审核报告（疑似只造标记未写报告）：{expected}"
            ],
            report_warns,
        )
    text = report_path.read_text(encoding="utf-8", errors="replace")
    if SECTION_TITLE not in text:
        return (
            "no-section",
            [
                f"报告校验：审核报告未出现「{SECTION_TITLE}」节标题（4.1e 可能未真正执行）：{report_path}"
            ],
            report_warns,
        )
    hits, strong_hits, strong_names = match_evidence(text)
    need_strong = REQUIRE_STRONG
    if len(hits) < MIN_HITS or (need_strong and not strong_hits):
        hit_txt = ", ".join(hits) if hits else "（无）"
        strong_txt = ", ".join(strong_hits) if strong_hits else "（无）"
        return (
            "weak-section",
            [
                "报告校验：报告含「人工证据包」标题但结构证据不足"
                f"（证据类别命中 {len(hits)}/≥{MIN_HITS}"
                + (" 且须含强证据：" + " / ".join(strong_names) if need_strong else "")
                + "；强证据=人工段/人工注入/段落位置与段号或字数邻接）"
                f"。命中类别：{hit_txt}；强证据：{strong_txt} → {report_path}"
            ],
            report_warns,
        )
    body = find_body(project, nnn, chapter)
    if body is not None:
        body_errs = check_body_claims(text, body)
        if body_errs:
            return "body-mismatch", body_errs, report_warns
    return "ok", [], report_warns


def main() -> int:
    parser = argparse.ArgumentParser(description="核验第 N 章写后流水线 .done 空标记")
    parser.add_argument("-p", "--project", required=True, help="项目（书名）目录")
    parser.add_argument("-c", "--chapter", required=True, type=int, help="章节号，如 12")
    parser.add_argument(
        "--no-chapter-done",
        action="store_true",
        help="流水线中途检查：不要求 chapter.done（闭环宣称禁止使用）",
    )
    parser.add_argument(
        "--include-chapter-done",
        action="store_true",
        help="兼容别名：默认已要求 chapter.done",
    )
    parser.add_argument(
        "--soft-report",
        action="store_true",
        help="报告/正文交叉校验失败仅警告（闭环宣称禁止使用）",
    )
    parser.add_argument(
        "--strict-report",
        action="store_true",
        help="兼容别名：默认已硬校验报告",
    )
    parser.add_argument(
        "--allow-non-empty",
        action="store_true",
        help="仅限用户本地调试：非空标记仍视为存在（宿主/LLM 闭环核验禁止使用）",
    )
    args = parser.parse_args()

    # 兼容：--strict-report 与默认硬校验一致；--include-chapter-done 与默认一致
    soft_report = bool(args.soft_report)
    require_chapter = not args.no_chapter_done

    chapter = args.chapter
    if chapter < 1:
        print(f"❌ 章节号无效：{chapter}（应为 ≥1 的整数）")
        return 1

    project = Path(args.project).expanduser()
    if not project.is_absolute():
        project = Path.cwd() / project
    if not project.is_dir():
        print(f"❌ 项目目录不存在：{project}")
        return 1

    done_dir = project / ".done"
    if not done_dir.is_dir():
        print(f"❌ 缺少 .done 目录：{done_dir}（项目可能未初始化写后流水线）")
        return 1

    nnn = f"{chapter:03d}"
    required = required_marks(chapter)
    present: list[str] = []
    nonempty: list[str] = []
    missing: list[str] = []
    name_conflicts: list[str] = []
    for mark in required:
        state, warns = classify_first(done_dir, nnn, chapter, f"_{mark}.done")
        name_conflicts.extend(warns)
        if state == "present":
            present.append(mark)
        elif state == "nonempty":
            if args.allow_non_empty:
                present.append(mark)
            else:
                nonempty.append(mark)
        else:
            missing.append(mark)

    chapter_state, chapter_warns = classify_first(done_dir, nnn, chapter, "_chapter.done")
    name_conflicts.extend(chapter_warns)
    has_chapter = chapter_state == "present" or (
        chapter_state == "nonempty" and args.allow_non_empty
    )
    has_zhuque = "zhuque" in present

    report_status, report_msgs, report_warns = check_report(
        project, nnn, chapter, has_zhuque
    )
    name_conflicts.extend(report_warns)
    report_fail = bool(report_msgs) and not soft_report

    report_mode = "soft" if soft_report else "hard"
    chapter_req = "required" if require_chapter else "skipped"
    print(f"【check-done】项目={project} 章节=第{nnn}章")
    print(
        f"必需标记数：{len(required)}（10 倍数章含 innovation；只认空标记 size=0；"
        f"chapter={chapter_req}；报告={report_mode}）"
    )
    print(f"已有（空）：{', '.join(present) if present else '（无）'}")
    print(f"非空无效：{', '.join(nonempty) if nonempty else '（无）'}")
    print(f"缺失：{', '.join(missing) if missing else '（无）'}")
    if chapter_state == "present":
        print("chapter.done：存在（空）")
    elif chapter_state == "nonempty":
        print(
            "chapter.done：存在但非空（无效）"
            if not args.allow_non_empty
            else "chapter.done：存在（非空，已兼容放行）"
        )
    else:
        print("chapter.done：不存在")
    print(f"报告校验：{report_status}（{report_mode}）")
    if name_conflicts:
        print("⚠️ 章号命名冲突：")
        for m in name_conflicts:
            print(f"  {m}")

    def print_report_msgs(prefix: str = "报告校验：") -> None:
        if not report_msgs:
            return
        print(prefix)
        for m in report_msgs:
            print(f"  {m}")

    if missing or nonempty:
        print()
        if missing:
            print(f"❌ 未闭环：缺 {len(missing)} 项必需 .done：{', '.join(missing)}")
        if nonempty:
            print(
                f"❌ 未闭环：{len(nonempty)} 项标记非空（标准=size0）：{', '.join(nonempty)}"
            )
            print("修复：清空对应 .done 文件内容（保留文件名），或删除后重写")
        if "zhuque" in missing:
            print(
                "提示：缺 zhuque → 须补跑 03_26 4.1b–4.1e 人工证据包（见 modules/03_26_功能模块.md）"
            )
        if has_chapter:
            print("异常：chapter.done 已存在但必需标记不齐/非空（疑似提前闭环/标记被删）")
        print("语义提醒：.done 齐全 ≠ 朱雀「人工>疑似」达标；达标以用户回传三态为准")
        if args.allow_non_empty:
            print("⚠️ 已启用 --allow-non-empty（仅限用户调试；宿主/LLM 禁止用此开关宣称闭环）")
        print_report_msgs("⚠️ 报告校验诊断：")
        print("下一步：主代理按漏步自愈矩阵补缺，或用户回复「补缺」/「写后」")
        return 1

    if require_chapter and not has_chapter:
        print()
        print("❌ 必需标记已齐，但缺有效 chapter.done（默认要求；中途检查用 --no-chapter-done）")
        print_report_msgs("⚠️ 报告校验诊断：")
        return 1

    if report_fail:
        print()
        print("❌ 报告校验未通过（默认硬校验；仅调试可用 --soft-report）：")
        for m in report_msgs:
            print(f"  {m}")
        return 1

    print()
    print(
        f"✅ 第{nnn}章必需空 .done 已齐（允许称流水线闭环；朱雀目标仍以用户回传三态为准）"
    )
    print(
        "对外汇报硬约定：必须分列「流水线闭环」与「朱雀三态/待检测」；"
        "禁止只贴 .done 或本脚本 exit0 冒充检测通过"
    )
    if args.allow_non_empty:
        print("⚠️ 已启用 --allow-non-empty（仅限用户调试；宿主/LLM 禁止用此开关宣称闭环）")
    if soft_report and report_msgs:
        print("⚠️ 报告校验警告（--soft-report 不阻断；闭环宣称禁止使用本开关）：")
        for m in report_msgs:
            print(f"  {m}")
    if not require_chapter and not has_chapter:
        print(
            "⚠️ 已启用 --no-chapter-done（仅限流水线中途检查；闭环宣称必须含 chapter.done）"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
