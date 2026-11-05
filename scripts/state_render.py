#!/usr/bin/env python3
"""状态单 → 派生视图渲染（state_commit 调用；亦可独立运行）。

【口径（与 state_commit.ps1 内联渲染逐字一致）】
- 度量/编码：UTF-8 无 BOM、换行 LF；所有内容由 `_state.json` 逐字段生成。
- 派生文件头含 `<!-- derived:state-render v1 | revision=N -->`，供人识别"手改无效"。
- 手改任何派生文件 → `state_commit check` 报 `derived view differs from _state.json` 并点名文件。

派生清单（相对 `记忆系统/`）：
  圣经/故事圣经.md        ← state.overview
  圣经/人物弧线.md        ← state.characters
  圣经/伏笔清单.md        ← state.foreshadow
  圣经/剧情时间线.md      ← state.timeline.readers_known
  圣经/作者真相时间线.md  ← state.timeline.authors_truth
  圣经/事实台账.md        ← state.facts
  上下文.md               ← state.context（固定 7 栏）
  摘要/第NNN章摘要.md     ← state.summaries（每章一文件）
不派生（保持手写，见总案开放问题#2 默认）：阶段/、卷/、约束/。

用法：
  python scripts/state_render.py --project "路径/书名"            # 重渲染并写入
  python scripts/state_render.py --project "路径/书名" --compare   # 只比对，不写（退出码 1=有差异）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MARKER_TPL = "<!-- derived:state-render v1 | revision={rev} -->\n"
STATE_REL = "记忆系统/_state.json"


def load_state(project: Path) -> dict:
    p = project / STATE_REL
    if not p.is_file():
        raise FileNotFoundError(f"缺少状态单：{p}")
    with p.open(encoding="utf-8-sig") as f:
        return json.load(f)


def _line(entry) -> str:
    """时间线/事实条目兼容 str 或 dict。"""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        parts = []
        for k in ("chapter", "章", "event", "事件", "subject", "主体", "fact", "事实", "value", "值", "time", "时间"):
            if k in entry and entry[k] not in (None, ""):
                parts.append(f"{entry[k]}")
        return " | ".join(parts) if parts else json.dumps(entry, ensure_ascii=False)
    return str(entry)


def render_views(state: dict) -> dict[str, str]:
    """返回 {相对记忆系统的路径: 文件内容}（确定性渲染，同输入必同输出）。"""
    rev = int(state.get("state_revision", 0))
    mk = MARKER_TPL.format(rev=rev)
    views: dict[str, str] = {}

    # 1) 故事圣经（overview）
    ov = state.get("overview", {}) or {}
    lines = [
        mk,
        "# 故事圣经",
        "",
        f"书名：{state.get('book_title', '')}",
        "",
        "## 项目概况",
        str(ov.get("summary", "")),
        "",
        "## 世界观简版",
        str(ov.get("world_brief", "")),
        "",
        "## 阶段划分",
    ]
    stages = ov.get("stages", []) or []
    if stages:
        for s in stages:
            lines.append(f"- {_line(s)}")
    else:
        lines.append("- （未登记）")
    lines += ["", "## 状态快照", str(ov.get("snapshot", "")), ""]
    views["圣经/故事圣经.md"] = "\n".join(lines)

    # 2) 人物弧线
    lines = [mk, "# 人物弧线", ""]
    chars = state.get("characters", {}) or {}
    if not chars:
        lines.append("（无登记角色）")
    for name in sorted(chars):
        c = chars[name] or {}
        lines.append(f"## {name}")
        aliases = c.get("aliases", []) or []
        if aliases:
            lines.append(f"别名：{'、'.join(aliases)}")
        lines.append(f"当前状态：{c.get('snapshot', '')}")
        facts = c.get("known_facts", []) or []
        if facts:
            lines.append("已知事实：")
            for fct in facts:
                lines.append(f"- {fct}")
        lines.append("")
    views["圣经/人物弧线.md"] = "\n".join(lines)

    # 3) 伏笔清单
    lines = [
        mk,
        "# 伏笔清单",
        "",
        "| 编号 | 线索 | 埋于 | 计划回收 | 已回收 | 优先级 |",
        "|---|---|---|---|---|---|",
    ]
    fs = state.get("foreshadow", {}) or {}
    if not fs:
        lines.append("| — | （无） | | | | |")
    for fid in sorted(fs):
        it = fs[fid] or {}
        resolved = it.get("resolved_ch")
        lines.append(
            f"| {fid} | {it.get('desc', '')} | {it.get('planted_ch', '')} "
            f"| {it.get('planned_resolve_ch', '')} | {resolved if resolved not in (None, '') else '未回收'} "
            f"| {it.get('priority', '')} |"
        )
    lines.append("")
    views["圣经/伏笔清单.md"] = "\n".join(lines)

    # 4/5) 时间线（读者已知 / 作者真相）
    tl = state.get("timeline", {}) or {}
    for rel, key, title in (
        ("圣经/剧情时间线.md", "readers_known", "剧情时间线（读者已知）"),
        ("圣经/作者真相时间线.md", "authors_truth", "作者真相时间线（未揭示）"),
    ):
        lines = [mk, f"# {title}", ""]
        items = tl.get(key, []) or []
        if not items:
            lines.append("（无条目）")
        for i, e in enumerate(items, 1):
            lines.append(f"{i}. {_line(e)}")
        lines.append("")
        views[rel] = "\n".join(lines)

    # 6) 事实台账
    lines = [mk, "# 事实台账", "", "| 章 | 主体 | 事实 | 值 |", "|---|---|---|---|"]
    facts = state.get("facts", []) or []
    if not facts:
        lines.append("| — | （无） | | |")
    for e in facts:
        if isinstance(e, dict):
            lines.append(
                f"| {e.get('chapter', '')} | {e.get('subject', '')} | {e.get('fact', '')} | {e.get('value', '')} |"
            )
        else:
            lines.append(f"|  | {_line(e)} |  |  |")
    lines.append("")
    views["圣经/事实台账.md"] = "\n".join(lines)

    # 7) 上下文（固定 7 栏）
    ctx = state.get("context", {}) or {}
    pos = ctx.get("position", {}) or {}
    def _dump(key):
        vals = ctx.get(key, []) or []
        return [f"- {v}" for v in vals] if vals else ["- （空）"]
    lines = [
        mk,
        "# 上下文卡",
        "",
        "## 当前位置",
        f"- 第 {pos.get('chapter', '?')} 章 | 卷：{pos.get('volume', '')} | 故事时间：{pos.get('story_time', '')}",
        "",
        "## 长期约束",
        *_dump("long_term_constraints"),
        "",
        "## 核心角色状态",
        *_dump("active_character_names"),
        "",
        "## 活跃伏笔",
        *([f"- {k}：{(fs.get(k) or {}).get('desc', '')}" for k in sorted(fs) if (fs.get(k) or {}).get("resolved_ch") in (None, "")] or ["- （无）"]),
        "",
        "## 近三章速记",
        *_dump("recent_chapters"),
        "",
        "## 下一章承诺",
        *_dump("next_chapter_commitments"),
        "",
        "## 本章应推进",
        _dump("next_chapter_commitments")[0] if ctx.get("next_chapter_commitments") else "- （空）",
        "",
    ]
    views["上下文.md"] = "\n".join(lines)

    # 8) 摘要（每章一文件）
    for ch in sorted((state.get("summaries", {}) or {}).keys(), key=lambda x: int(x)):
        nnn = f"{int(ch):03d}"
        views[f"摘要/第{nnn}章摘要.md"] = f"{mk}\n# 第{nnn}章摘要\n\n{state['summaries'][ch]}\n"

    return views


MANIFEST_REL = ".story/derived-manifest.json"


def _manifest_path(project: Path) -> Path:
    return project / MANIFEST_REL


def _load_manifest(project: Path) -> list[str]:
    """上一轮由本模块写过的派生件清单（只用于清理自己的旧产物，绝不触碰手写件）。"""
    try:
        d = json.loads(_manifest_path(project).read_text(encoding="utf-8-sig"))
        files = d.get("files") if isinstance(d, dict) else None
        return [str(x) for x in files] if isinstance(files, list) else []
    except (OSError, ValueError):
        return []


def _save_manifest(project: Path, files) -> None:
    p = _manifest_path(project)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"files": sorted(files)}, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8", newline="\n")
    except OSError:
        pass  # 清单写失败不影响派生正确性，只是下一轮少清理


def write_views(project: Path, views: dict[str, str]) -> list[str]:
    base = project / "记忆系统"
    written: list[str] = []
    old = _load_manifest(project)
    for rel, text in views.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        written.append(rel)
    # 清理上一轮写过、本轮 state 已无对应条目的派生件（否则残留会被当上下文读，且 check 看不出来）
    for rel in sorted(set(old) - set(views)):
        p = base / rel
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass
    _save_manifest(project, views.keys())
    return written


def compare_views(project: Path, views: dict[str, str]) -> list[str]:
    """返回不一致的相对路径列表（缺文件/内容不同/旧派生残留都算不一致）。"""
    base = project / "记忆系统"
    bad: list[str] = []
    for rel, text in views.items():
        p = base / rel
        if not p.is_file():
            bad.append(f"{rel}（缺失）")
            continue
        cur = p.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
        if cur != text:
            bad.append(rel)
    for rel in sorted(set(_load_manifest(project)) - set(views)):
        if rel.startswith("摘要/"):
            continue  # 摘要自有 stale 检查点名（缺/多余），这里不重复判红
        if (base / rel).is_file():
            bad.append(f"{rel}（残留：state 已无该条目）")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description="状态单派生视图渲染")
    ap.add_argument("--project", required=True, help="项目（书名）目录")
    ap.add_argument("--compare", action="store_true", help="只比对不写入")
    args = ap.parse_args()
    project = Path(args.project).expanduser()
    if not project.is_absolute():
        project = Path.cwd() / project
    if not project.is_dir():
        print(f"❌ 项目目录不存在：{project}")
        return 1
    try:
        state = load_state(project)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"❌ {exc}")
        return 1
    try:
        views = render_views(state)
    except Exception as exc:  # 独立入口 fail-closed：状态单类型非法不吐裸 Traceback
        print(f"❌ 渲染派生视图失败（状态单类型非法：{exc}）")
        return 1
    if args.compare:
        bad = compare_views(project, views)
        if bad:
            print(f"❌ derived view differs from _state.json（{len(bad)} 个文件）：")
            for b in bad:
                print(f"  - 记忆系统/{b}")
            return 1
        print(f"✅ 派生视图与状态单一致（{len(views)} 个文件）")
        return 0
    written = write_views(project, views)
    print(f"✅ 已渲染 {len(written)} 个派生文件（revision={state.get('state_revision', 0)}）")
    for w in written:
        print(f"  - 记忆系统/{w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
