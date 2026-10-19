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
  python scripts/check-done.py -p . -c 12 --require-task-sheet   # 写后闭环：校验细纲任务书四段+必落拍全勾

退出码：0=必需空标记齐且默认硬校验通过；1=缺标记/非空/路径错误/硬校验失败/参数错误（与 ps1 一致，仅 0/1）。
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


def _i64(s):
    # Int64 范围收口（对齐 ps1 [long] 绑定失败 rc1 / check-doc-budget _in_i64 先例）
    _t = str(s)
    if not re.fullmatch(r"-?[0-9]+", _t):  # 纯 ASCII 十进制（int(…,10) 会接受下划线/非 ASCII 数字如 3_0/١٢，ps1 [long] 绑定拒绝 → 反向裁决）
        raise argparse.ArgumentTypeError(f"非十进制整数：{s}")
    try:
        v = int(_t, 10)
    except ValueError:
        raise argparse.ArgumentTypeError(f"非十进制整数：{s}")
    if not (-2**63 <= v < 2**63):
        raise argparse.ArgumentTypeError(f"超出 Int64 范围：{s}")
    return v


class _ArgParser(argparse.ArgumentParser):
    """退出码契约仅 0/1（与 check-done.ps1 参数绑定失败=1 对齐）：用法错误也走 1。"""

    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"❌ 参数错误：{message}", file=sys.stderr)
        raise SystemExit(1)


def load_rules() -> dict:
    if not RULES_PATH.is_file():
        print(f"❌ 缺少规则单源：{RULES_PATH}")
        raise SystemExit(1)
    try:
        with RULES_PATH.open(encoding="utf-8-sig") as f:
            rules = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"❌ 规则单源解析失败：{RULES_PATH}")
        print(f"解析详情：{exc}", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(rules, dict):
        print(f"❌ 规则单源解析失败：{RULES_PATH}（须为 JSON 对象）")
        raise SystemExit(1)
    errs: list[str] = []
    for key in ("base_marks", "evidence_rules", "section_title", "min_hits",
                "require_strong", "claim_para_pattern", "body_globs", "task_sheet"):
        if key not in rules:
            errs.append(f"缺 {key}")
    bm = rules.get("base_marks")
    if "base_marks" in rules:
        if not isinstance(bm, list):
            errs.append("base_marks 须为数组")
        elif not all(isinstance(x, str) for x in bm):
            errs.append("base_marks 元素须为字符串")
    if "evidence_rules" in rules:
        if not isinstance(rules["evidence_rules"], list):
            errs.append("evidence_rules 须为数组")
        elif not all(isinstance(r, dict) and {"name", "pattern", "strong"} <= set(r) for r in rules["evidence_rules"]):
            errs.append("evidence_rules[i] 须为含 name/pattern/strong 的对象")
        else:
            for _er in rules["evidence_rules"]:
                _ep = _er["pattern"]
                if not isinstance(_ep, str):
                    errs.append("evidence_rules[i].pattern 须为字符串")
                elif re.search(r"\(\?P<|\(\?<|\(\?P=", _ep):
                    errs.append("evidence_rules[i].pattern 须为跨引擎安全语法（禁命名组/命名回引 (?P<、(?<、(?P=）")
                else:
                    try:
                        re.compile(_ep)
                    except re.error:
                        errs.append("evidence_rules[i].pattern 正则无效")
    if "section_title" in rules and not isinstance(rules["section_title"], str):
        errs.append("section_title 须为字符串")
    if "min_hits" in rules:
        mh = rules["min_hits"]
        if not (isinstance(mh, int) and not isinstance(mh, bool)):
            errs.append("min_hits 须为整数")
    if "claim_para_pattern" in rules:
        if not isinstance(rules["claim_para_pattern"], str):
            errs.append("claim_para_pattern 须为字符串")
        else:
            _cp = rules["claim_para_pattern"]
            # 命名组/命名回引两方言互不兼容（py 只认 (?P<x>、ps1 只认 (?<x>）→ 统一在双引擎拒收，跨引擎才可判
            if re.search(r"\(\?P<|\(\?<|\(\?P=", _cp):
                errs.append("claim_para_pattern 须为跨引擎安全语法（禁命名组/命名回引 (?P<、(?<、(?P=）")
            else:
                try:
                    re.compile(_cp)
                except re.error:
                    errs.append("claim_para_pattern 正则无效")
    if "body_globs" in rules:
        if not isinstance(rules["body_globs"], list):
            errs.append("body_globs 须为数组")
        elif not all(isinstance(x, str) for x in rules["body_globs"]):
            errs.append("body_globs 元素须为字符串")
    if "task_sheet" in rules:
        ts = rules["task_sheet"]
        if not isinstance(ts, dict):
            errs.append("task_sheet 须为对象")
        else:
            for sub, typ, lab in (("dirs", list, "dirs"), ("suffix", str, "suffix"),
                                  ("required_sections", list, "required_sections"),
                                  ("required_item_mark", str, "required_item_mark")):
                if sub not in ts:
                    errs.append(f"task_sheet 缺 {sub}")
                elif not isinstance(ts[sub], typ):
                    errs.append(f"task_sheet.{lab} 类型错误")
                elif typ is list and sub in ("dirs", "required_sections") and not all(isinstance(x, str) for x in ts[sub]):
                    errs.append(f"task_sheet.{lab} 元素须为字符串")
    if errs:
        print(f"❌ 规则单源校验失败：{RULES_PATH}（{'；'.join(errs)}）")
        raise SystemExit(1)
    return rules


# 惰性加载（对齐 ps1:65→70→78 顺序：项目目录 → .done → 规则单源；导入期不加载，复合故障首条归因与 ps1 一致）
RULES: dict = {}
BASE_MARKS: list[str] = []
EVIDENCE_RULES: list[tuple[str, str, bool]] = []
SECTION_TITLE: str = ""
MIN_HITS: int = 0
REQUIRE_STRONG: bool = False
CLAIM_PARA_RE: re.Pattern[str] | None = None
STATE_MARK: dict = {}
BODY_GLOBS: list[str] = []


def _init_rules() -> None:
    global RULES, BASE_MARKS, EVIDENCE_RULES, SECTION_TITLE, MIN_HITS, REQUIRE_STRONG, CLAIM_PARA_RE, STATE_MARK, BODY_GLOBS, TASK_SHEET, TASK_DIRS, TASK_SUFFIX, TASK_SECTIONS, TASK_ITEM_MARK
    RULES = load_rules()
    BASE_MARKS = list(RULES["base_marks"])
    EVIDENCE_RULES = [(r["name"], r["pattern"], bool(r["strong"])) for r in RULES["evidence_rules"]]
    SECTION_TITLE = RULES["section_title"]
    MIN_HITS = int(RULES["min_hits"])
    REQUIRE_STRONG = bool(RULES["require_strong"])
    CLAIM_PARA_RE = re.compile(RULES["claim_para_pattern"])
    STATE_MARK = RULES.get("state_mark", {})
    BODY_GLOBS = list(RULES["body_globs"])
    TASK_SHEET = RULES["task_sheet"]
    TASK_DIRS = list(TASK_SHEET["dirs"])
    TASK_SUFFIX = TASK_SHEET["suffix"]
    TASK_SECTIONS = list(TASK_SHEET["required_sections"])
    TASK_ITEM_MARK = TASK_SHEET["required_item_mark"]

# 章节任务书结构门禁（P0-2）——同为规则单源派生，随 _init_rules 惰性加载
TASK_SHEET: dict = {}
TASK_DIRS: list[str] = []
TASK_SUFFIX: str = ""
TASK_SECTIONS: list[str] = []
TASK_ITEM_MARK: str = ""


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
    # 约定：首块为章节标题时段号从正文块起算（标题不计）。
    # 标题判定（与 check-done.ps1 Count-BodyParagraphs 逐字同口径）：
    # 块数>1 且首块前 40 个 UTF-16 码元内含「第」或「章」→ 视为标题。
    def _head40(s: str) -> str:
        return s.encode("utf-16-le")[:80].decode("utf-16-le", errors="ignore")

    if len(blocks) > 1:
        is_heading = any(x in _head40(blocks[0]) for x in ("第", "章"))
        body_blocks = blocks[1:] if is_heading else blocks
    else:
        is_heading = False
        body_blocks = blocks
    if is_heading:
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
    def _guard_dir(cands) -> Path | None:
        # 目录冒充正文 → 双引擎同文案 rc1（对 ps1:30「目录当报告等场景不得 exit0 放行」纪律）
        for p in cands:
            if p.is_dir():
                print(f"❌ 正文交叉校验：正文候选路径是目录（应为 .txt 文件）：{p}")
                raise SystemExit(1)
        return None
    for glob in BODY_GLOBS:
        pattern = glob.format(nnn=nnn, ch=chapter)
        # 排序取首个：文件系统 glob 顺序不定，与 ps1 的 Sort-Object Name 对齐
        _c1 = sorted(body_dir.parent.glob(pattern.replace("\\", "/")))
        _guard_dir(_c1)
        for p in _c1:
            if p.is_file():
                return p
        _c2 = sorted(body_dir.glob(Path(pattern).name))
        _guard_dir(_c2)
        for p in _c2:
            if p.is_file():
                return p
    # 兜底：前缀匹配
    for prefix in (f"第{nnn}章", f"第{chapter}章"):
        matches = sorted(body_dir.glob(f"{prefix}*.txt"))
        _guard_dir(matches)
        if matches:
            return matches[0]
    return None


def _try_i32(s: object) -> int | None:
    """对齐 .NET Int32.TryParse：去前导零后仅 ASCII 十进制、≤10 位且 ≤ 2^31-1。"""
    txt = s if isinstance(s, str) else ("" if s is None else str(s))
    t = txt.lstrip("0") or "0"
    if not re.fullmatch(r"[0-9]{1,10}", t):
        return None
    n = int(t)
    return n if n <= 2_147_483_647 else None


def check_body_claims(report_text: str, body: Path) -> list[str]:
    """报告声明段号 vs 正文段落数；返回错误消息列表。"""
    claimed = []
    for m in CLAIM_PARA_RE.findall(report_text):
        n = _try_i32(m)  # 对齐 ps1 Int32.TryParse：仅 ASCII 十进制且 ≤ 2^31-1，其余安全跳过
        if n is not None:
            claimed.append(n)
    if not claimed:
        return []
    try:
        body_text = body.read_text(encoding="utf-8-sig", errors="replace")
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
    text = report_path.read_text(encoding="utf-8-sig", errors="replace")
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


def check_task_sheet(
    project: Path, nnn: str, chapter: int
) -> tuple[str, list[str]]:
    """章节任务书结构门禁（写后闭环用）。返回 (状态, 错误列表)。

    状态：skipped（未启用）/ missing / no-section / no-item / unchecked / ok
    规则：细纲/第NNN章_细纲.md 必须含四段（TASK/GOAL/MUST DO/CONTEXT）；
    MUST DO 内至少 1 条带「（必落）」的清单项，且写后须全部勾选 [x]。
    """
    sheet_path = None
    expected_parts: list[str] = []
    for rel in TASK_DIRS:
        sheet_dir = project / rel
        found, _warns = resolve_existing(sheet_dir, nnn, chapter, TASK_SUFFIX)
        expected_parts.extend(
            str(sheet_dir / n) for n in _candidate_names(nnn, chapter, TASK_SUFFIX)
        )
        if found is not None and found.is_file():
            sheet_path = found
            break
    if sheet_path is None:
        return "missing", [
            "任务书校验：缺章节任务书（写前应构造、写后必须存在）："
            + " / ".join(expected_parts)
        ]
    text = sheet_path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    headings: dict[str, int] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        for sec in TASK_SECTIONS:
            if stripped.startswith(f"### {sec}") and sec not in headings:
                headings[sec] = i
    missing_secs = [s for s in TASK_SECTIONS if s not in headings]
    if missing_secs:
        return "no-section", [
            f"任务书校验：缺段 {', '.join(missing_secs)}"
            f"（必需四段：{'/'.join(TASK_SECTIONS)}）→ {sheet_path}"
        ]
    if "MUST DO" not in TASK_SECTIONS:
        return "config-error", [
            "任务书校验：check-done-rules.json 的 task_sheet.required_sections 缺 MUST DO"
            "（节拍清单锚点不可移除/改名）→ 修改规则单源后需同步双实现测试"
        ]
    # MUST DO 块范围：从其标题到下一个同级/更高级标题
    start = headings["MUST DO"]
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip().startswith("### "):
            end = i
            break
    item_re = re.compile(r"^\s*-\s*\[([ xX])\][^\n]*" + re.escape(TASK_ITEM_MARK))
    items: list[tuple[bool, str]] = []
    for line in lines[start + 1 : end]:
        m = item_re.match(line)
        if m:
            items.append((m.group(1).lower() == "x", line.strip()))
    if not items:
        return "no-item", [
            f"MUST DO 缺「{TASK_ITEM_MARK}」节拍清单项（不允许边写边想）→ {sheet_path}"
        ]
    unchecked = [label for done, label in items if not done]
    if unchecked:
        return "unchecked", [
            f"任务书校验：漏必落拍 {len(unchecked)}/{len(items)}（写后须全勾，否则本章判未完成）："
            + "；".join(unchecked)
        ]
    return "ok", []


def main() -> int:
    parser = _ArgParser(description="核验第 N 章写后流水线 .done 空标记")
    parser.add_argument("-p", "--project", required=True, help="项目（书名）目录")
    parser.add_argument("-c", "--chapter", required=True, type=_i64, help="章节号，如 12")
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
    parser.add_argument(
        "--require-task-sheet",
        action="store_true",
        help="写后闭环：校验细纲章节任务书四段齐全且必落拍全勾（缺则 exit1）",
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

    _init_rules()  # 规则单源在项目/.done 检查后加载（对齐 ps1 诊断顺序）

    nnn = f"{chapter:03d}"
    required = required_marks(chapter)
    # P0-3：存在 记忆系统/_state.json → 追加 state 标记（无 state 文件的存量项目不追加）
    state_rel = STATE_MARK.get("state_file", "记忆系统/_state.json")
    state_file = project / state_rel
    state_on = bool(STATE_MARK.get("auto_when_state_file", True)) and state_file.is_file()
    state_errs: list[str] = []
    if state_on:
        required = required + [str(STATE_MARK.get("name", "state"))]
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
    if state_on and STATE_MARK.get("require_last_committed", True):
        try:
            with state_file.open(encoding="utf-8-sig") as fh:
                _st = json.load(fh)
            # 顶层非对象（数组/标量）→ 与 last_committed 缺失同文案（中文兜底，不裸崩）
            _last = _st.get("last_committed_chapter") if isinstance(_st, dict) else None
            if not (isinstance(_last, int) and not isinstance(_last, bool)
                    and -(2 ** 63) <= _last < 2 ** 63):
                state_errs.append(f"状态单校验：last_committed_chapter 不是整数 → {state_file}")
            elif _last < chapter:
                state_errs.append(
                    f"状态单落后：last_committed_chapter={_last} < 第{chapter}章"
                    f"（先 state_commit commit --chapter {chapter} 再闭环）"
                )
        except (OSError, json.JSONDecodeError) as exc:
            state_errs.append(f"状态单校验：无法读取 {state_file}：{exc}")
    _state_line = (
        f"状态单：required（{state_rel} 存在）"
        if state_on
        else (
            "状态单：skipped（auto_when_state_file=false，走旧路径）"
            if not STATE_MARK.get("auto_when_state_file", True)
            else "状态单：skipped（无 _state.json，走旧路径）"
        )
    )
    print(_state_line)
    if args.require_task_sheet:
        task_status, task_errs = check_task_sheet(project, nnn, chapter)
        print(f"任务书校验：{task_status}（require-task-sheet）")
    else:
        task_status, task_errs = "skipped", []
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
        if state_on and str(STATE_MARK.get("name", "state")) in missing:
            print(
                f"提示：缺 {str(STATE_MARK.get('name', 'state'))} → 须先跑 scripts/state_commit.py commit --chapter N"
                "（派生一致性用 state_commit check 核验）"
            )
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

    if state_errs:
        print()
        print("❌ 状态单校验未通过：")
        for m in state_errs:
            print(f"  {m}")
        return 1

    if task_errs:
        print()
        print("❌ 任务书校验未通过（--require-task-sheet）：")
        for m in task_errs:
            print(f"  {m}")
        print("修复：补写 细纲/第NNN章_细纲.md 任务书四段并勾选必落拍（模板见 templates/章节任务书模板.md）")
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
