#!/usr/bin/env python3
"""状态单唯一写入方（权威实现 py；与 scripts/state_commit.ps1 同口径）。

【单源】规则字段见本文件顶部常量；派生渲染在 scripts/state_render.py（本文件调用）。
【路径】项目内：记忆系统/_state.json（权威）| 记忆系统/.state-commit.lock（乐观锁）
        事务 JSON 只写 {项目}/.story/work/，提交成功后自动删除；失败保留（改完重跑同一份）。
【兼容】无 _state.json 的存量项目：本工具不介入（check-done 也不要求 state 标记）。

用法：
  python scripts/state_commit.py init    --project DIR [--input FILE]
  python scripts/state_commit.py draft   --project DIR --chapter N
  python scripts/state_commit.py commit  --project DIR --chapter N --input TXN.json
  python scripts/state_commit.py check   --project DIR
  python scripts/state_commit.py migrate --project DIR [--apply]

退出码：0=成功/通过；1=校验失败、stale、派生不一致或环境错误。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_render  # noqa: E402  （同目录模块）

SCHEMA_VERSION = 1
STATE_REL = Path("记忆系统/_state.json")
DRAFT_REL = Path("记忆系统/_state.migrate-draft.json")
LOCK_REL = Path("记忆系统/.state-commit.lock")
LOCK_STALE_SECONDS = 300
WORK_DIR = Path(".story/work")
DONE_SUFFIX = "_state.done"

REQUIRED_KEYS = (
    "schema_version", "state_revision", "book_title", "last_committed_chapter",
    "imported_through_chapter", "characters", "foreshadow", "timeline", "facts",
    "context", "wordcounts",
)
DERIVED_MANAGED = (  # migrate --apply 归档对象（阶段/卷/约束保持手写，不动）
    ("圣经",), ("摘要",),  # 目录：整体移入 _旧存档/
)
ARCHIVE_ROOT = Path("_旧存档")  # 相对 记忆系统/ 的归档目录名


def _die(msg: str) -> int:
    print(f"❌ {msg}")
    return 1


def load_json(p: Path) -> dict:
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def state_path(project: Path) -> Path:
    return project / STATE_REL


def validate_state(s: dict) -> list[str]:
    errs: list[str] = []
    if not isinstance(s, dict):
        return ["状态单不是 JSON 对象"]
    if s.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version 应为 {SCHEMA_VERSION}，实际 {s.get('schema_version')!r}")
    for k in REQUIRED_KEYS:
        if k not in s:
            errs.append(f"缺字段：{k}")
    if "state_revision" in s and not isinstance(s["state_revision"], int):
        errs.append("state_revision 必须是整数（乐观锁比较用）")
    tl = s.get("timeline", {})
    if not isinstance(tl, dict) or not all(k in tl for k in ("readers_known", "authors_truth")):
        errs.append("timeline 必须含 readers_known / authors_truth 两键")
    ctx = s.get("context", {})
    if not isinstance(ctx, dict):
        errs.append("context 必须是对象")
    return errs


def default_state(book_title: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "state_revision": 0,
        "book_title": book_title,
        "last_committed_chapter": 0,
        "imported_through_chapter": 0,
        "overview": {"summary": "", "world_brief": "", "stages": [], "snapshot": ""},
        "characters": {},
        "foreshadow": {},
        "timeline": {"readers_known": [], "authors_truth": []},
        "facts": [],
        "summaries": {},
        "context": {
            "position": {"chapter": 0, "volume": "", "story_time": ""},
            "long_term_constraints": [],
            "active_character_names": [],
            "recent_chapters": [],
            "next_chapter_commitments": [],
        },
        "wordcounts": {},
    }


# ---------------- 锁 ----------------
def acquire_lock(project: Path) -> tuple[Path | None, str]:
    lock = project / LOCK_REL
    lock.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": os.getpid(), "ts": int(time.time())}, ensure_ascii=False)
    for attempt in (1, 2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            return lock, "ok"
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0
            if age > LOCK_STALE_SECONDS and attempt == 1:
                print(f"⚠️ 发现 {LOCK_STALE_SECONDS}s 以上的残留锁，接管（{lock}）")
                try:
                    lock.unlink()
                except OSError:
                    return None, "锁被占用且无法清除"
                continue
            return None, f"已有提交在进行中（锁 {lock}，{int(age)}s 前创建）"
    return None, "锁获取失败"


def release_lock(lock: Path | None) -> None:
    if lock is not None:
        try:
            lock.unlink()
        except OSError:
            pass


# ---------------- delta 合并 ----------------
def apply_delta(state: dict, delta: dict) -> None:
    """dict 递归合并；facts/timeline.* 追加；其余（含 context 列表）以 delta 为准整体替换。"""
    for key, val in delta.items():
        if key in ("facts",):
            state.setdefault(key, [])
            state[key].extend(val or [])
        elif key == "timeline" and isinstance(val, dict):
            tl = state.setdefault("timeline", {"readers_known": [], "authors_truth": []})
            for sub, items in val.items():
                tl.setdefault(sub, [])
                tl[sub].extend(items or [])
        elif key == "summaries" and isinstance(val, dict):
            state.setdefault("summaries", {}).update(val)
        elif key == "wordcounts" and isinstance(val, dict):
            state.setdefault("wordcounts", {}).update(val)
        elif isinstance(val, dict) and isinstance(state.get(key), dict):
            _deep_merge(state[key], val)
        else:
            state[key] = val


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def write_state(project: Path, state: dict) -> None:
    p = state_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, p)


def touch_done(project: Path, chapter: int) -> Path:
    d = project / ".done"
    d.mkdir(parents=True, exist_ok=True)
    nnn = f"{chapter:03d}"
    p = d / f"第{nnn}章{DONE_SUFFIX}"
    p.write_bytes(b"")
    return p


# ---------------- 子命令 ----------------
def cmd_init(project: Path, input_file: Path | None) -> int:
    if state_path(project).is_file():
        return _die(f"已存在 {STATE_REL}（如需重来请先手工移除，禁止静默覆盖）")
    payload: dict = {}
    if input_file is not None:
        if not input_file.is_file():
            return _die(f"输入不存在：{input_file}")
        payload = load_json(input_file)
    st = default_state(str(payload.get("book_title") or project.name))
    for k in ("overview", "context"):
        if isinstance(payload.get(k), dict):
            _deep_merge(st[k], payload[k])
    for k in ("characters", "foreshadow", "wordcounts", "summaries"):
        if isinstance(payload.get(k), dict):
            st[k].update(payload[k])
    if isinstance(payload.get("facts"), list):
        st["facts"] = payload["facts"]
    errs = validate_state(st)
    if errs:
        return _die("init 输入校验失败：" + "；".join(errs))
    write_state(project, st)
    (project / WORK_DIR).mkdir(parents=True, exist_ok=True)
    views = state_render.render_views(st)
    written = state_render.write_views(project, views)
    print(f"✅ init 完成：{STATE_REL}（revision=0）+ 派生 {len(written)} 个文件")
    return 0


def cmd_draft(project: Path, chapter: int) -> int:
    sp = state_path(project)
    if not sp.is_file():
        return _die(f"缺少 {STATE_REL}（先 init 或 migrate --apply）")
    st = load_json(sp)
    txn_dir = project / WORK_DIR / f"第{chapter:03d}章"
    txn_dir.mkdir(parents=True, exist_ok=True)
    txn_path = txn_dir / "state.json"
    txn = {
        "chapter": chapter,
        "expected_state_revision": st.get("state_revision", 0),
        "mode": "commit",
        "_hint": "只填 delta：变化角色快照 / 新增伏笔 / 本章事实 / 时间线条目 / 摘要 / 字数；不要重写整份 state",
        "delta": {
            "characters": {},
            "foreshadow": {},
            "facts": [],
            "timeline": {"readers_known": [], "authors_truth": []},
            "summaries": {},
            "wordcounts": {},
            "context": {},
        },
    }
    txn_path.write_text(json.dumps(txn, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"✅ 草稿已生成：{txn_path.relative_to(project).as_posix()}（expected_state_revision={txn['expected_state_revision']}）")
    return 0


def cmd_commit(project: Path, chapter: int, input_file: Path | None) -> int:
    sp = state_path(project)
    if not sp.is_file():
        return _die(f"缺少 {STATE_REL}（先 init 或 migrate --apply）")
    if input_file is None or not input_file.is_file():
        return _die("事务文件缺失（--input）；用 draft 生成后填写再提交")
    # 1) 校验失败 → 改事务重跑（保留事务文件）
    try:
        txn = load_json(input_file)
    except json.JSONDecodeError as exc:
        return _die(f"事务 JSON 解析失败（改事务重跑）：{exc}")
    if txn.get("chapter") != chapter:
        return _die(f"事务 chapter={txn.get('chapter')} 与 --chapter {chapter} 不符（改事务重跑）")
    if not isinstance(txn.get("expected_state_revision"), int):
        return _die("事务缺 expected_state_revision（整数；改事务重跑）")
    if not isinstance(txn.get("delta"), dict):
        return _die("事务缺 delta 对象（改事务重跑）")
    st = load_json(sp)
    errs = validate_state(st)
    if errs:
        return _die("现有状态单校验失败（先修复再提交）：" + "；".join(errs))
    if txn["expected_state_revision"] != st["state_revision"]:
        return _die(
            f"stale transaction：expected_state_revision={txn['expected_state_revision']}，"
            f"当前 state_revision={st['state_revision']}（重跑 draft 取新 revision 再提交）"
        )
    # 2) 上锁（失败=环境问题，保留事务，修正后重跑同一份）
    lock, msg = acquire_lock(project)
    if lock is None:
        return _die(f"写入失败：{msg}（修正环境后重跑同一份 commit，事务文件已保留）")
    try:
        # 双检（锁内再读一次，防并发覆盖）
        st = load_json(sp)
        if txn["expected_state_revision"] != st["state_revision"]:
            return _die(
                f"stale transaction（锁内复核）：expected={txn['expected_state_revision']} "
                f"actual={st['state_revision']}"
            )
        apply_delta(st, txn["delta"])
        st["state_revision"] = int(st["state_revision"]) + 1
        st["last_committed_chapter"] = max(int(st.get("last_committed_chapter", 0)), chapter)
        write_state(project, st)
        views = state_render.render_views(st)
        state_render.write_views(project, views)
        done = touch_done(project, chapter)
    except OSError as exc:
        return _die(f"写入失败：{exc}（修正环境后重跑同一份 commit，事务文件已保留）")
    finally:
        release_lock(lock)
    # 3) 成功才删事务
    try:
        input_file.unlink()
    except OSError:
        pass
    print(f"✅ state.commit ok：chapter={chapter} revision→{st['state_revision']} "
          f"last_committed_chapter={st['last_committed_chapter']}")
    print(f"✅ 派生已刷新（{len(views)} 个文件）；标记 {done.relative_to(project).as_posix()}")
    print("提示：.done/第NNN章_state.done 为闭环必需标记（check-done 在存在 _state.json 时自动要求）")
    return 0


def cmd_check(project: Path) -> int:
    sp = state_path(project)
    if not sp.is_file():
        return _die(f"缺少 {STATE_REL}（存量项目走旧路径即可，无需本命令）")
    try:
        st = load_json(sp)
    except json.JSONDecodeError as exc:
        return _die(f"状态单 JSON 损坏：{exc}")
    errs = validate_state(st)
    views = state_render.render_views(st)
    bad = state_render.compare_views(project, views)
    lock = project / LOCK_REL
    lock_note = ""
    if lock.is_file():
        age = time.time() - lock.stat().st_mtime
        lock_note = f"；⚠️ 残留锁 {int(age)}s" + ("（超过 300s 下次提交会接管）" if age > LOCK_STALE_SECONDS else "（<300s，可能有提交在进行）")
    print(f"【state-commit check】{project}")
    print(f"revision={st.get('state_revision')} last_committed_chapter={st.get('last_committed_chapter')} "
          f"派生文件={len(views)}{lock_note}")
    if errs:
        print(f"❌ 状态单校验失败（{len(errs)} 项）：")
        for e in errs:
            print(f"  - {e}")
        return 1
    if bad:
        print(f"❌ derived view differs from _state.json（{len(bad)} 个文件）：")
        for b in bad:
            print(f"  - 记忆系统/{b}")
        print("修复：不要手改派生文件；改 state 后重跑 commit（或 state_render 重新渲染）")
        return 1
    print("✅ 派生视图与状态单逐字一致（check 二次运行仍应 PASS）")
    return 0


def _existing_chapters(project: Path) -> list[int]:
    out: list[int] = []
    summary_dir = project / "记忆系统/摘要"
    if summary_dir.is_dir():
        for f in summary_dir.glob("第*章摘要.md"):
            mm = re.match(r"第(\d+)章摘要\.md$", f.name)
            if mm:
                out.append(int(mm.group(1)))
    return sorted(out)


def cmd_migrate(project: Path, apply: bool) -> int:
    mem = project / "记忆系统"
    if not mem.is_dir():
        return _die(f"记忆系统 不存在：{mem}")
    sp = state_path(project)
    if sp.is_file() and not apply:
        return print(f"✅ 已存在 {STATE_REL}，无需重复 migrate") or 0
    draft_path = project / DRAFT_REL
    # 候选旧文件：圣经/*.md + 摘要/*.md + 上下文.md（阶段/卷/约束为手写区，不归档）
    olds: list[Path] = []
    for sub in ("圣经", "摘要"):
        d = mem / sub
        if d.is_dir():
            olds += sorted(p for p in d.glob("*.md"))
    ctx = mem / "上下文.md"
    if ctx.is_file():
        olds.append(ctx)

    if not apply:
        title = project.name
        bible = mem / "圣经/故事圣经.md"
        if bible.is_file():
            first = bible.read_text(encoding="utf-8", errors="replace").lstrip().splitlines()
            if first and first[0].startswith("#"):
                title = first[0].lstrip("# ").strip() or title
        chapters = _existing_chapters(project)
        st = default_state(title)
        st["imported_through_chapter"] = max(chapters) if chapters else 0
        st["overview"]["snapshot"] = f"（迁移草案：由 {len(olds)} 个旧文件生成，关键内容需人工补进本 state）"
        draft_path.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"✅ 迁移草案已生成：{DRAFT_REL.as_posix()}（**尚未生效**）")
        print(f"   - 待人工补全字段：characters / foreshadow / facts / timeline / context（草案只带书名与章数）")
        print(f"   - 待归档旧文件：{len(olds)} 个（阶段/卷/约束不动）")
        for o in olds:
            print(f"     · {o.relative_to(project).as_posix()}")
        print(f"   - 确认无误后执行：migrate --project \"{project.as_posix()}\" --apply（归档旧文件 + 写入权威 state + 渲染派生）")
        return 0

    # --apply
    if not draft_path.is_file():
        return _die(f"缺草案 {DRAFT_REL.as_posix()}（先跑一次不带 --apply 的 migrate 生成草案并人工确认）")
    st = load_json(draft_path)
    errs = validate_state(st)
    if errs:
        return _die("草案校验失败：" + "；".join(errs))
    # 归档（只移动、永不删除）
    moved: list[str] = []
    for o in olds:
        rel = o.relative_to(mem)
        target = mem / ARCHIVE_ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return _die(f"归档目标已存在（先人工处理）：{target}")
        shutil.move(str(o), str(target))
        moved.append(target.relative_to(project).as_posix())
    write_state(project, st)
    views = state_render.render_views(st)
    state_render.write_views(project, views)
    draft_path.unlink()
    print(f"✅ migrate --apply 完成：{STATE_REL}（revision=0，last_committed_chapter={st['last_committed_chapter']}）")
    print(f"   归档旧文件 {len(moved)} 个 → {ARCHIVE_ROOT.as_posix()}/（只移动、未删除）")
    print(f"   重渲染派生 {len(views)} 个文件")
    print("⚠️ 新派生内容目前只含草案字段：请把关键信息补进 _state.json 后重跑 commit 或 state_render，并用 check 验证")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="状态单唯一写入方")
    ap.add_argument("command", choices=["init", "draft", "commit", "check", "migrate"])
    ap.add_argument("--project", required=True, help="项目（书名）目录")
    ap.add_argument("--chapter", type=int, default=0, help="章节号（draft/commit 用）")
    ap.add_argument("--input", default=None, help="输入 JSON（init 的 init.json / commit 的事务文件）")
    ap.add_argument("--apply", action="store_true", help="migrate 确认执行（归档旧文件并写入权威 state）")
    args = ap.parse_args()
    project = Path(args.project).expanduser()
    if not project.is_absolute():
        project = Path.cwd() / project
    if not project.is_dir():
        return _die(f"项目目录不存在：{project}")
    if args.command == "init":
        return cmd_init(project, Path(args.input) if args.input else None)
    if args.command in ("draft", "commit") and args.chapter < 1:
        return _die("--chapter 必须 ≥1")
    if args.command == "draft":
        return cmd_draft(project, args.chapter)
    if args.command == "commit":
        return cmd_commit(project, args.chapter, Path(args.input) if args.input else None)
    if args.command == "check":
        return cmd_check(project)
    return cmd_migrate(project, args.apply)


if __name__ == "__main__":
    sys.exit(main())
