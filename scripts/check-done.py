#!/usr/bin/env python3
"""章节 .done 客观核验（与 scripts/check-done.ps1 同口径）。

只认 `[书名]/.done/` 空标记；不读正文、不代替补跑。
`.done_zhuque` 存在 = 4.1b–4.1e 步骤已登记 ≠ 朱雀「人工>疑似」达标。

用法：
  python scripts/check-done.py --project "路径/书名" --chapter 12
  python scripts/check-done.py -p . -c 10 --include-chapter-done

退出码：0=必需标记齐；1=缺标记或路径错误
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_MARKS = [
    "wordcount",
    "bible",
    "summary",
    "zhuque",
    "gates",
    "constraints",
    "logic_causal",
    "review",
    "quality",
    "coherence",
    "rhythm",
    "merge",
]


def required_marks(chapter: int) -> list[str]:
    marks = list(BASE_MARKS)
    if chapter % 10 == 0:
        marks.append("innovation")
    return marks


def main() -> int:
    parser = argparse.ArgumentParser(description="核验第 N 章写后流水线 .done 标记")
    parser.add_argument("-p", "--project", required=True, help="项目（书名）目录")
    parser.add_argument("-c", "--chapter", required=True, type=int, help="章节号，如 12")
    parser.add_argument(
        "--include-chapter-done",
        action="store_true",
        help="同时要求 第NNN章_chapter.done 存在",
    )
    args = parser.parse_args()

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
    missing: list[str] = []
    for mark in required:
        path = done_dir / f"第{nnn}章_{mark}.done"
        if path.exists():
            present.append(mark)
        else:
            missing.append(mark)

    chapter_file = done_dir / f"第{nnn}章_chapter.done"
    has_chapter = chapter_file.exists()

    print(f"【check-done】项目={project} 章节=第{nnn} 章")
    print(f"必需标记数：{len(required)}（10 倍数章含 innovation）")
    print(f"已有：{', '.join(present) if present else '（无）'}")
    print(f"缺失：{', '.join(missing) if missing else '（无）'}")
    print(f"chapter.done：{'存在' if has_chapter else '不存在'}")

    if missing:
        print()
        print(f"❌ 未闭环：缺 {len(missing)} 项必需 .done：{', '.join(missing)}")
        if "zhuque" in missing:
            print("提示：缺 zhuque → 须补跑 03_26 4.1b–4.1e 人工证据包（见 modules/03_26_功能模块.md）")
        if has_chapter:
            print("异常：chapter.done 已存在但必需标记不齐（疑似提前闭环/标记被删）")
        print("语义提醒：.done 齐全 ≠ 朱雀「人工>疑似」达标；达标以用户回传三态为准")
        print("下一步：主代理按漏步自愈矩阵补缺，或用户回复「补缺」/「写后」")
        return 1

    if args.include_chapter_done and not has_chapter:
        print()
        print("❌ 必需标记已齐，但缺 chapter.done（--include-chapter-done）")
        return 1

    print()
    print(f"✅ 第{nnn}章必需 .done 已齐（允许称流水线闭环；朱雀目标仍以用户回传三态为准）")
    if not has_chapter:
        print(
            "备注：chapter.done 尚未写入——若流水线尚未走到步骤4.4/章节档案，请继续执行；"
            "若已闭环请补写 chapter.done"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
