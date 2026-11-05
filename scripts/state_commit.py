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
    # 参数错误收敛 rc1 + 中文（对齐 check-done/check-doc-budget/detect；ps1 绑定失败亦 rc1）
    def error(self, message):
        print(f"❌ 参数错误：{message}")
        raise SystemExit(1)


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
    # 损坏 JSON → 中文兜底 rc1（对齐 ps1 ConvertFrom-Json 的 try/catch 托管输出）
    with p.open(encoding="utf-8-sig") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as exc:
            print(f"❌ 状态单 JSON 损坏：{exc}")
            sys.exit(1)


def state_path(project: Path) -> Path:
    return project / STATE_REL


def validate_state(s: dict) -> list[str]:
    errs: list[str] = []
    if not isinstance(s, dict):
        return ["状态单不是 JSON 对象"]
    _sv = s.get("schema_version")
    if not (isinstance(_sv, int) and not isinstance(_sv, bool)) or _sv != SCHEMA_VERSION:
        _sv_disp = int(_sv) if isinstance(_sv, float) and _sv.is_integer() else (_sv if _sv is not None else "None")
        errs.append(f"schema_version 应为 {SCHEMA_VERSION}，实际 {_sv_disp}")
    for k in REQUIRED_KEYS:
        if k not in s:
            errs.append(f"缺字段：{k}")
    # 三标量：键存在即须为 Int64 内整数（显式 null/超界与 ps1 [int]/[long] 判型同判，fail-closed）
    for _sk, _smsg in (("state_revision", "state_revision 必须是整数（乐观锁比较用）"),
                       ("imported_through_chapter", "imported_through_chapter 必须是整数"),
                       ("last_committed_chapter", "last_committed_chapter 必须是整数")):
        if _sk in s and not (isinstance(s[_sk], int) and not isinstance(s[_sk], bool)
                             and -(2 ** 63) <= s[_sk] < 2 ** 63):
            errs.append(_smsg)
    _summ = s.get("summaries")
    if _summ is not None and not isinstance(_summ, dict):
        errs.append("summaries 须为对象")
    if isinstance(_summ, dict) and not all(isinstance(k, str) and re.fullmatch(r"[0-9]{1,18}", k) for k in _summ):
        errs.append("summaries 键必须是十进制数字字符串（≤18 位）")
    elif isinstance(_summ, dict):
        _seen: dict[int, str] = {}
        for _k in _summ:
            _n = int(_k)
            if _n in _seen:  # "1" 与 "001" 渲染到同一 摘要/第001章摘要.md，后者会静默覆盖
                errs.append(f"summaries 撞键：{_seen[_n]} 与 {_k} 指向同一章（渲染会互相覆盖）")
                break
            _seen[_n] = _k
    tl = s.get("timeline", {})
    if not isinstance(tl, dict) or not all(k in tl for k in ("readers_known", "authors_truth")):
        errs.append("timeline 必须含 readers_known / authors_truth 两键")
    if "context" in s and not isinstance(s.get("context"), dict):
        errs.append("context 必须是对象")
    ctx = s.get("context")
    if isinstance(ctx, dict) and ctx.get("position") is not None and not isinstance(ctx["position"], dict):
        errs.append("context.position 须为对象")
    ov = s.get("overview")
    if ov is not None and not isinstance(ov, dict):
        errs.append("overview 须为对象")
    if isinstance(ov, dict) and ov.get("stages") is not None and not isinstance(ov["stages"], list):
        errs.append("overview.stages 须为数组")
    wcv = s.get("wordcounts")
    if wcv is not None and not isinstance(wcv, dict):
        errs.append("wordcounts 须为对象")
    fv = s.get("facts")
    if fv is not None and not isinstance(fv, list):
        errs.append("facts 须为数组")
    elif isinstance(fv, list):
        for _fi2, _fe2 in enumerate(fv):
            if not isinstance(_fe2, dict):
                errs.append(f"facts[{_fi2}] 须为对象")
                break
    if isinstance(ov, dict) and isinstance(ov.get("stages"), list):
        for _se in ov["stages"]:
            if not isinstance(_se, (str, dict)):
                errs.append("overview.stages 元素须为字符串或对象")
                break
    _tl = s.get("timeline", {})
    if isinstance(_tl, dict):
        for _tk in ("readers_known", "authors_truth"):
            if _tl.get(_tk) is not None and not isinstance(_tl[_tk], list):
                errs.append(f"timeline.{_tk} 须为数组")
            elif isinstance(_tl.get(_tk), list):
                for _te in _tl[_tk]:
                    if not isinstance(_te, (str, dict)):
                        errs.append(f"timeline.{_tk} 元素须为字符串或对象")
                        break
    fsv = s.get("foreshadow")
    if fsv is not None and not isinstance(fsv, dict):
        errs.append("foreshadow 须为对象")
    elif isinstance(fsv, dict):
        _seenFs = set()
        for _fsn, _fsv in fsv.items():
            if not _fsn:
                errs.append("foreshadow 键名不能为空（空键 pwsh7 ConvertFrom-Json 拒读）")
                break
            if _fsn.lower() in _seenFs:
                errs.append(f"foreshadow 键名大小写撞键：{_fsn}（pwsh7 ConvertFrom-Json 拒读同名大小写变体）")
                break
            _seenFs.add(_fsn.lower())
            if not isinstance(_fsv, dict):
                errs.append(f"foreshadow.{_fsn} 须为对象")
                break
            for _fk in ("planted_ch", "planned_resolve_ch", "resolved_ch"):
                if _fk in _fsv and _fsv[_fk] is not None and not (
                        isinstance(_fsv[_fk], int) and not isinstance(_fsv[_fk], bool)
                        and 0 <= _fsv[_fk] < 2 ** 63):
                    errs.append(f"foreshadow.{_fsn}.{_fk} 必须是非负整数")
                    break
    _ov2 = s.get("overview")
    if isinstance(_ov2, dict) and _ov2.get("summary") is not None and not isinstance(_ov2["summary"], str):
        errs.append("overview.summary 须为字符串")
    _ctx2 = s.get("context")
    if isinstance(_ctx2, dict):
        for _ck in ("recent_chapters", "next_chapter_commitments", "characters_present", "mystery_knowledge", "goals", "long_term_constraints"):
            if _ctx2.get(_ck) is not None and not isinstance(_ctx2[_ck], list):
                errs.append(f"context.{_ck} 须为数组")
    _chvv = s.get("characters")
    if isinstance(_chvv, dict):
        for _cn3, _cv3 in _chvv.items():
            if isinstance(_cv3, dict):
                for _ak in ("aliases", "known_facts"):
                    if _cv3.get(_ak) is not None and not isinstance(_cv3[_ak], list):
                        errs.append(f"characters.{_cn3}.{_ak} 须为数组")
                    elif isinstance(_cv3.get(_ak), list):
                        for _ae in _cv3[_ak]:
                            if not isinstance(_ae, str):
                                errs.append(f"characters.{_cn3}.{_ak} 元素须为字符串")
                                break
    if isinstance(fv, list):
        for _fi, _fe in enumerate(fv):
            if isinstance(_fe, dict) and _fe.get("chapter") is not None and (not isinstance(_fe["chapter"], int) or isinstance(_fe["chapter"], bool) or abs(_fe["chapter"]) > 9223372036854775807):
                errs.append(f"facts[{_fi}].chapter 须为整数")
    chv = s.get("characters")
    if chv is not None and not isinstance(chv, dict):
        errs.append("characters 须为对象")
    if isinstance(chv, dict):
        for _cn, _cv in chv.items():
            if not _cn:
                errs.append("characters 键名不能为空（空键 pwsh7 ConvertFrom-Json 拒读）")
                break
            if _cv is not None and not isinstance(_cv, dict):
                errs.append(f"characters.{_cn} 须为对象")
                break
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
def apply_delta(state: dict, delta: dict, chapter: int | None = None) -> None:
    """dict 递归合并；facts/timeline.* 追加（同章重跑按章替换/逐条去重，保证 commit 幂等）；其余（含 context 列表）以 delta 为准整体替换。"""
    for key, val in delta.items():
        if key in ("facts",):
            state.setdefault(key, [])
            new = list(val or [])
            if chapter is not None:
                # 同章重跑=替换该章条目：03_26 五·状态提交的失败处置要求"重跑同一份 commit"，
                # 若只追加会让同批事实按次成倍重复（数据静默污染且 check 仍绿）
                state[key] = [e for e in state[key]
                              if not (isinstance(e, dict) and e.get("chapter") == chapter)]
            for e in new:
                if e not in state[key]:
                    state[key].append(e)
        elif key == "timeline" and isinstance(val, dict):
            tl = state.setdefault("timeline", {"readers_known": [], "authors_truth": []})
            for sub, items in val.items():
                tl.setdefault(sub, [])
                for e in (items or []):
                    if e not in tl[sub]:  # 同一份事务重跑不重复追加
                        tl[sub].append(e)
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
        return _die(f"已存在 {STATE_REL.as_posix()}（如需重来请先手工移除，禁止静默覆盖）")
    chapters = _existing_chapters(project)
    if chapters:
        return _die(f"检测到存量章节（摘要至第{chapters[-1]}章）——init 会以空渲染覆盖手写圣经；存量项目升级请走 migrate（草案 + --apply）")
    bible = project / "记忆系统" / "圣经" / "故事圣经.md"
    if bible.is_file():
        head = bible.read_text(encoding="utf-8-sig", errors="replace")[:200]
        if "derived:state-render" not in head:
            print("⚠️ 检测到非派生圣经文件：init 将以空渲染覆盖其内容（存量/已填充项目请改用 migrate）")
    payload: dict = {}
    if input_file is not None:
        if not input_file.is_file():
            return _die(f"输入不存在：{input_file}")
        payload = load_json(input_file)
    if not isinstance(payload, dict):
        return _die(f"init 输入须为 JSON 对象：{input_file}（顶层非对象，如数组/标量）")  # 对齐 ps1 判型（原裸 traceback AttributeError）
    st = default_state(str(payload.get("book_title") or project.name))
    for k in ("overview", "context"):
        if isinstance(payload.get(k), dict):
            _deep_merge(st[k], payload[k])
        elif k in payload:
            st[k] = payload[k]  # 非对象交由 validate_state 报错（对齐 ps1 拒绝口径）
    for k in ("characters", "foreshadow", "wordcounts", "summaries"):
        if isinstance(payload.get(k), dict):
            st[k].update(payload[k])
        elif k in payload:
            st[k] = payload[k]  # 同上
    if "facts" in payload:
        st["facts"] = payload["facts"]  # 非数组交由 validate_state 报「facts 须为数组」（对齐 ps1 拒绝口径）
    errs = validate_state(st)
    if errs:
        return _die("init 输入校验失败：" + "；".join(errs))
    write_state(project, st)
    (project / WORK_DIR).mkdir(parents=True, exist_ok=True)
    views = state_render.render_views(st)
    written = state_render.write_views(project, views)
    print(f"✅ init 完成：{STATE_REL.as_posix()}（revision=0）+ 派生 {len(written)} 个文件")
    return 0


def cmd_draft(project: Path, chapter: int) -> int:
    sp = state_path(project)
    if not sp.is_file():
        return _die(f"缺少 {STATE_REL.as_posix()}（先 init 或 migrate --apply）")
    st = load_json(sp)
    txn_dir = project / WORK_DIR / f"第{chapter:03d}章"
    txn_dir.mkdir(parents=True, exist_ok=True)
    txn_path = txn_dir / "state.json"
    txn = {
        "chapter": chapter,
        "expected_state_revision": st.get("state_revision", 0),
        "mode": "commit",
        "_hint": "只填 delta：变化角色快照 / 新增伏笔 / 本章事实 / 时间线条目 / 摘要 / 字数 / 变化时的 overview（状态快照·阶段）与 context（位置·近三章·下一章承诺）；不要重写整份 state",
        "delta": {
            "characters": {},
            "foreshadow": {},
            "facts": [],
            "timeline": {"readers_known": [], "authors_truth": []},
            "summaries": {},
            "wordcounts": {},
            "overview": {},
            "context": {},
        },
    }
    txn_path.write_text(json.dumps(txn, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"✅ 草稿已生成：{txn_path.relative_to(project).as_posix()}（expected_state_revision={txn['expected_state_revision']}）")
    return 0


def cmd_commit(project: Path, chapter: int, input_file: Path | None) -> int:
    sp = state_path(project)
    if not sp.is_file():
        return _die(f"缺少 {STATE_REL.as_posix()}（先 init 或 migrate --apply）")
    if input_file is None or not input_file.is_file():
        return _die("事务文件缺失（--input）；用 draft 生成后填写再提交")
    # 1) 校验失败 → 改事务重跑（保留事务文件）
    try:
        txn = load_json(input_file)
    except json.JSONDecodeError as exc:
        return _die(f"事务 JSON 解析失败（改事务重跑）：{exc}")
    if not isinstance(txn, dict):
        return _die(f"事务须为 JSON 对象：{input_file}（顶层非对象；改事务重跑）")  # 对齐 ps1 同文案 rc1
    _chv = txn.get("chapter")
    if not (isinstance(_chv, int) and not isinstance(_chv, bool)) or _chv != chapter:
        _chv_disp = int(_chv) if isinstance(_chv, float) and _chv.is_integer() else _chv
        return _die(f"事务 chapter={_chv_disp} 与 --chapter {chapter} 不符（改事务重跑）")
    _exp = txn.get("expected_state_revision")
    if not (isinstance(_exp, int) and not isinstance(_exp, bool)):
        return _die("事务缺 expected_state_revision（整数；改事务重跑）")
    if not isinstance(txn.get("delta"), dict):
        return _die("事务缺 delta 对象（改事务重跑）")
    _d = txn["delta"]
    for _k in ("summaries", "wordcounts", "overview", "context", "characters", "timeline", "foreshadow"):
        if _k in _d and _d[_k] is not None and not isinstance(_d[_k], dict):
            return _die(f"delta.{_k} 须为对象（改事务重跑）")
    if "facts" in _d and _d["facts"] is not None and not isinstance(_d["facts"], list):
        return _die("delta.facts 须为数组（改事务重跑）")
    _dtl = _d.get("timeline")
    if isinstance(_dtl, dict):
        for _tk2 in ("readers_known", "authors_truth"):
            if _dtl.get(_tk2) is not None and not isinstance(_dtl[_tk2], list):
                return _die(f"delta.timeline.{_tk2} 须为数组（改事务重跑）")
    _dctx = _d.get("context")
    if isinstance(_dctx, dict):
        for _ck2 in ("recent_chapters", "next_chapter_commitments", "characters_present", "mystery_knowledge", "goals", "long_term_constraints"):
            if _dctx.get(_ck2) is not None and not isinstance(_dctx[_ck2], list):
                return _die(f"delta.context.{_ck2} 须为数组（改事务重跑）")
    _dc = _d.get("context")
    if isinstance(_dc, dict) and _dc.get("position") is not None and not isinstance(_dc["position"], dict):
        return _die("delta.context.position 须为对象（改事务重跑）")
    _dov = _d.get("overview")
    if isinstance(_dov, dict) and _dov.get("stages") is not None and not isinstance(_dov["stages"], list):
        return _die("delta.overview.stages 须为数组（改事务重跑）")
    _dch = _d.get("characters")
    if isinstance(_dch, dict):
        for _cn2, _cv2 in _dch.items():
            if _cv2 is not None and not isinstance(_cv2, dict):
                return _die(f"delta.characters.{_cn2} 须为对象（改事务重跑）")
    _dsum = _d.get("summaries")
    if isinstance(_dsum, dict) and not all(isinstance(k, str) and re.fullmatch(r"[0-9]{1,18}", k) for k in _dsum):
        return _die("summaries 键必须是十进制数字字符串（≤18 位）")
    st = load_json(sp)
    errs = validate_state(st)
    if errs:
        return _die("现有状态单校验失败（先修复再提交）：" + "；".join(errs))
    if int(st["state_revision"]) >= 2 ** 63 - 1:
        return _die("state_revision 已到 Int64 上界，自增会溢出（拒绝提交；请人工归零并同步派生视图）")
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
        apply_delta(st, txn["delta"], chapter)
        _verr = validate_state(st)
        if _verr:
            return _die("合并结果复验失败（改 delta 后重跑同一份 commit）：" + "；".join(_verr))
        st["state_revision"] = int(st["state_revision"]) + 1
        st["last_committed_chapter"] = max(int(st.get("last_committed_chapter", 0)), chapter)
        try:
            views = state_render.render_views(st)  # 先渲染后落盘：渲染崩溃不半提交，事务保留可重跑
        except Exception as exc:
            return _die(f"渲染派生视图失败（状态单类型非法：{exc}）")
        write_state(project, st)
        try:
            state_render.write_views(project, views)
        except OSError as exc:
            return _die(f"写入失败：派生视图未刷新（状态单已写入、事务已过期）：{exc}。修环境后跑 state_render.py --project 补渲染 + state_commit check 复核")
        try:
            done = touch_done(project, chapter) if chapter >= 1 else None
        except OSError as exc:
            # 到此 state 与派生已落盘、revision 已推进：重跑 commit 必 stale，故给补标记路径而非"重跑"
            return _die(f"写入失败：状态单与派生已刷新，但 .done 标记未创建：{exc}"
                        f"（勿重跑 commit，会因 stale 被拒；补建 .done/第{chapter:03d}章_state.done 空标记后跑 state_commit check 复核）")
    except OSError as exc:
        return _die(f"写入失败：{exc}（修正环境后重跑同一份 commit，事务文件已保留）")
    finally:
        release_lock(lock)
    # 3) 成功才删事务
    try:
        input_file.unlink()
    except OSError:
        pass
    _rvd = st["state_revision"] if isinstance(st["state_revision"], int) and not isinstance(st["state_revision"], bool) else "非法"
    _lcv = st["last_committed_chapter"] if isinstance(st["last_committed_chapter"], int) and not isinstance(st["last_committed_chapter"], bool) else "非法"
    print(f"✅ state.commit ok：chapter={chapter} revision→{_rvd} last_committed_chapter={_lcv}")
    if done is not None:
        print(f"✅ 派生已刷新（{len(views)} 个文件）；标记 {done.relative_to(project).as_posix()}")
        print("提示：.done/第NNN章_state.done 为闭环必需标记（check-done 在存在 _state.json 时自动要求）")
    else:
        print(f"✅ 派生已刷新（{len(views)} 个文件）；章节 0 种子提交不产生 .done 标记")
    return 0


def cmd_check(project: Path) -> int:
    sp = state_path(project)
    if not sp.is_file():
        return _die(f"缺少 {STATE_REL.as_posix()}（存量项目走旧路径即可，无需本命令）")
    try:
        st = load_json(sp)
    except json.JSONDecodeError as exc:
        return _die(f"状态单 JSON 损坏：{exc}")
    errs = validate_state(st)
    views = {}
    if not errs:  # 有校验错即跳过渲染（派生文件=0），与 ps1 同口径，避免渲染容差差异造成计数分叉
        try:
            views = state_render.render_views(st)
        except Exception as exc:  # 类型非法但未被 validate 捕获时也不裸崩：中文退出
            return _die(f"渲染派生视图失败（状态单类型非法：{exc}）")
    bad = state_render.compare_views(project, views) if views else []
    lock = project / LOCK_REL
    lock_note = ""
    if lock.is_file():
        age = time.time() - lock.stat().st_mtime
        lock_note = f"；⚠️ 残留锁 {int(age)}s" + ("（超过 300s 下次提交会接管）" if age > LOCK_STALE_SECONDS else "（<300s，可能有提交在进行）")
    print(f"【state-commit check】{project}")
    _rv = st.get("state_revision") if isinstance(st, dict) else None
    _lcvh = st.get("last_committed_chapter") if isinstance(st, dict) else None  # 非对象已由 validate 报「不是 JSON 对象」，headline 不裸崩
    def _hv(v):
        # headline 归一：非 Int64 内整数统一显示「非法」（对齐 ps1）
        if isinstance(v, int) and not isinstance(v, bool) and -(2 ** 63) <= v < 2 ** 63:
            return v
        return "非法"
    print(f"revision={_hv(_rv)} last_committed_chapter={_hv(_lcvh)} "
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
    # OF-6 摘要陈旧检测（警告不阻断：范围 1..last_committed，缺单章摘要即 stale）
    _last = int(st.get("last_committed_chapter", 0) or 0)
    _summ = st.get("summaries") or {}
    # 只按已有正文的章判 stale（O(实际章数)：last_committed 可达 3e9，线性扫 1..last 会挂死；无正文的章不需要摘要）
    _bodies = sorted({int(m.group(1)) for f in (project / "正文").glob("第*章*") if f.is_file() and (m := re.match(r"第([0-9]+)章", f.name))})
    _stale = [c for c in _bodies if c <= _last and f"{c:03d}" not in _summ and str(c) not in _summ]  # 键零填充/裸数字双形查表（键 "7" 合法）
    if _stale:
        _shown = "、".join(f"第{c:03d}章" for c in _stale[:10])
        if len(_stale) > 10:
            _shown += f"…（共 {len(_stale)} 章）"
        print(f"⚠️ 摘要陈旧（stale）：{_shown} 缺单章摘要 → 上下文可能不完整（先补摘要再续写）")
    else:
        print("✅ 摘要连续（stale=0）")
    return 0


def _existing_chapters(project: Path) -> list[int]:
    out: list[int] = []
    summary_dir = project / "记忆系统/摘要"
    if summary_dir.is_dir():
        for f in summary_dir.glob("第*章摘要.md"):
            mm = re.match(r"第([0-9]+)章摘要\.md$", f.name)
            if mm:
                out.append(int(mm.group(1)))
    return sorted(out)


def _rollback_moves(moved: list[tuple[Path, Path]]) -> tuple[int, int]:
    """把已归档文件移回原位；原位已被占用则留在归档区（绝不覆盖）。返回 (回滚数, 未回滚数)。"""
    back = stuck = 0
    for src, target in reversed(moved):
        try:
            if src.exists():
                stuck += 1
                continue
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(src))
            back += 1
        except OSError:
            stuck += 1
    return back, stuck


def cmd_migrate(project: Path, apply: bool) -> int:
    mem = project / "记忆系统"
    if not mem.is_dir():
        return _die(f"记忆系统 不存在：{mem}")
    sp = state_path(project)
    if sp.is_file() and not apply:
        return print(f"✅ 已存在 {STATE_REL.as_posix()}，无需重复 migrate") or 0
    draft_path = project / DRAFT_REL
    # 候选旧文件：只归档会被重渲染/可再生的派生件（圣经派生视图 + 伏笔时间线）+ 摘要 + 上下文；
    # 非派生文件（如 圣经/角色数据库.md）保留原位——归档且无渲染入口会使 03_26 步骤0 / 00 步骤B 缺件
    archive_names = {
        "故事圣经.md", "人物弧线.md", "剧情时间线.md", "伏笔清单.md",
        "作者真相时间线.md", "事实台账.md", "伏笔时间线.md",
    }
    olds: list[Path] = []
    for sub in ("圣经", "摘要"):
        d = mem / sub
        if d.is_dir():
            if sub == "圣经":
                olds += sorted(p for p in d.glob("*.md") if p.name in archive_names)
            else:
                # 只归档可由 state.summaries 重建的 第NNN章摘要；模板.md 等非派生件留原位
                olds += sorted(p for p in d.glob("*.md") if re.fullmatch(r"第[0-9]+章摘要\.md", p.name))  # [0-9] 收口对齐 _existing_chapters（\d 认 Unicode 数字族）
    ctx = mem / "上下文.md"
    if ctx.is_file():
        olds.append(ctx)

    if not apply:
        title = project.name
        bible = mem / "圣经/故事圣经.md"
        if bible.is_file():
            first = bible.read_text(encoding="utf-8-sig", errors="replace").lstrip().splitlines()
            if first and first[0].startswith("#"):
                title = first[0].lstrip("# ").strip() or title
        chapters = _existing_chapters(project)
        st = default_state(title)
        st["imported_through_chapter"] = max(chapters) if chapters else 0
        st["overview"]["snapshot"] = f"（迁移草案：由 {len(olds)} 个旧文件生成，关键内容需人工补进本 state）"
        draft_path.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"✅ 迁移草案已生成：{DRAFT_REL.as_posix()}（**尚未生效**）")
        print("   - 待人工补全字段：characters / foreshadow / facts / timeline / context / summaries（与 wordcounts；草案只带书名与章数；characters 按保留原位的 圣经/角色数据库.md 摘填，timeline.authors_truth 按归档的 作者真相时间线.md 摘填，summaries 按归档的 摘要/第NNN章摘要.md 摘填——摘要/模板.md 保留原位）")
        if (mem / "圣经/角色数据库.md").is_file():
            print("   - 保留原位（非派生，不归档）：圣经/角色数据库.md")
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
    # 归档（只移动、永不删除）：先全量预检冲突再动手；任一移动/写盘失败即回滚，避免半迁移
    plan: list[tuple[Path, Path]] = []
    for o in olds:
        rel = o.relative_to(mem)
        target = mem / ARCHIVE_ROOT / rel
        if target.exists():
            return _die(f"归档目标已存在（先人工处理）：{target}（未移动任何文件）")
        plan.append((o, target))
    moved: list[tuple[Path, Path]] = []
    wrote_state = False
    try:
        for o, target in plan:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(o), str(target))
            moved.append((o, target))
        write_state(project, st)
        wrote_state = True
        views = state_render.render_views(st)
        state_render.write_views(project, views)
    except Exception as exc:  # noqa: BLE001 —— 归档/渲染/写盘任一步失败都要回滚，保留可重试状态
        if wrote_state:
            try:
                state_path(project).unlink()
            except OSError:
                pass
        back, stuck = _rollback_moves(moved)
        return _die(f"migrate --apply 失败：{exc}；已回滚 {back} 个归档文件"
                    + (f"，{stuck} 个因原位被占未回滚（原件保留在 {ARCHIVE_ROOT.as_posix()}/）" if stuck else ""))
    draft_path.unlink()
    print(f"✅ migrate --apply 完成：{STATE_REL.as_posix()}（revision=0，last_committed_chapter={st['last_committed_chapter']}）")
    print(f"   归档旧文件 {len(moved)} 个 → {ARCHIVE_ROOT.as_posix()}/（只移动、未删除）")
    print(f"   重渲染派生 {len(views)} 个文件")
    print("⚠️ 新派生内容目前只含草案字段：请把关键信息补进 _state.json 后重跑 commit 或 state_render，并用 check 验证（summaries 未补则 摘要/ 不会重建，00 步骤B 会因缺摘要终止）")
    return 0


def main() -> int:
    ap = _ArgParser(description="状态单唯一写入方")
    ap.add_argument("command", choices=["init", "draft", "commit", "check", "migrate"])
    ap.add_argument("--project", required=True, help="项目（书名）目录")
    ap.add_argument("--chapter", type=_i64, default=0, help="章节号（draft/commit 用）")
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
    if args.command in ("draft", "commit") and args.chapter < 0:
        return _die("--chapter 必须 ≥0（--chapter 0 = 大纲阶段种子提交，不产生 .done 标记）")
    if args.command == "draft":
        return cmd_draft(project, args.chapter)
    if args.command == "commit":
        return cmd_commit(project, args.chapter, Path(args.input) if args.input else None)
    if args.command == "check":
        return cmd_check(project)
    return cmd_migrate(project, args.apply)


if __name__ == "__main__":
    sys.exit(main())
