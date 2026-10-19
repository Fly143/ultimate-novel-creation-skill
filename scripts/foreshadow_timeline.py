#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""foreshadow_timeline.py —— 伏笔时间线可视化渲染（MU-1，P3-21）。

数据源（自动选择）：
  1. 新架构：记忆系统/_state.json 的 foreshadow（planted_ch/planned_resolve_ch/resolved_ch/priority）
  2. 旧项目：记忆系统/圣经/伏笔清单.md 表格（表头含「首现+状态」；列名含 首现/预期回收）
产物：记忆系统/圣经/伏笔时间线.md —— 过期未回收点名 + 三段状态表 + Mermaid gantt 时间线。
Mermaid 把第 N 章映射为 2000-01-01 起第 N 天；过期=crit(红)、临近=active(蓝)、已回收=done(灰)。
生成物为派生件（头部 generated 标记），禁手改；本脚本可随时重跑覆盖。
双实现对拍：与 foreshadow_timeline.ps1 输出逐字节一致。
用法：
  python scripts/foreshadow_timeline.py --project "路径/书名" [--current-ch 120]
"""
import argparse
import json
import os
import re
import sys
from datetime import date, timedelta

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


MARK = "<!-- generated:foreshadow-timeline v1 | 派生件禁手改，重跑本脚本再生成 -->"
SRC_NEW, SRC_OLD = "state", "清单"
TITLE_BAD = re.compile(r"[:,：，]")


def day_of(ch: int) -> str:
    d = max(ch, 1) - 1
    if d > 2_913_088:  # date 9999-12-31 上界（2000-01-01 + 2,913,088 天）；超界双引擎同文案 rc1（不再裸 OverflowError）
        print(f"❌ 章号超出日期换算上界：{ch}（≤2913088 才能映射到 9999 年内）")
        sys.exit(1)
    return (date(2000, 1, 1) + timedelta(days=d)).strftime("%Y-%m-%d")


def digits(s: str):
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None


def detect_current(project: str) -> int:
    best = 0
    body = os.path.join(project, "正文")
    if os.path.isdir(body):
        for f in os.listdir(body):
            m = re.match(r"第(\d+)章", f)
            if m:
                best = max(best, int(m.group(1)))
    st = os.path.join(project, "记忆系统", "_state.json")
    if best == 0 and os.path.exists(st):
        try:
            with open(st, encoding="utf-8-sig") as f:
                best = int(json.load(f).get("last_committed_chapter", 0) or 0)
        except Exception:
            best = 0
    return best


def load_state(project: str):
    p = os.path.join(project, "记忆系统", "_state.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8-sig") as f:
        try:
            state = json.load(f)
        except ValueError:
            print("❌ 记忆系统/_state.json JSON 损坏（先 state_commit check 修复状态单）")
            sys.exit(1)
    if not isinstance(state, dict):
        print("❌ 记忆系统/_state.json 须为对象（先 state_commit check 修复状态单）")
        sys.exit(1)
    return state


def from_state(state) -> list[dict]:
    items = []
    fs = state.get("foreshadow")
    if fs is not None and not isinstance(fs, dict):
        print("❌ foreshadow 须为对象（先 state_commit check 修复状态单）")
        sys.exit(1)
    for fid, v in (fs or {}).items():
        if not isinstance(v, dict):
            print(f"❌ foreshadow.{fid} 须为对象（先 state_commit check 修复状态单）")
            sys.exit(1)
        def _numch(x):
            # 与 ps1 同口径：None/false/空串按 0；数值/数值串 Truncate 截断；负数钳 0（ps1 靠 -gt 0 过滤，等价）
            if x is None or x is False or x == "":
                return 0
            if isinstance(x, bool):
                raise ValueError("bool 不是章号")
            try:
                return max(0, int(float(x)))
            except (TypeError, ValueError):
                raise ValueError(f"章号不是数值：{x!r}")
        _rs_n = _numch(v.get("resolved_ch"))
        try:
            items.append({
                "id": fid,
                "name": str(v.get("desc") or ""),  # 显式 null 与 ps1 [string]$null 渲染同为空串
                "planted": _numch(v.get("planted_ch", 0)),
                "planned": _numch(v.get("planned_resolve_ch", 0)),
                "resolved": _rs_n,
                "status": "resolved" if _rs_n > 0 else "open",
            })
        except ValueError as exc:
            print(f"❌ foreshadow.{fid} 章号非法：{exc}")
            sys.exit(1)
    return items


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def from_checklist(path: str) -> list[dict]:
    items = []
    with open(path, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    header = None
    idx = {}
    seq = 0
    for line in lines:
        if not line.strip().startswith("|"):
            header = None
            idx = {}
            continue
        cells = split_row(line)
        if not cells:
            continue
        if header is None:
            joined = "".join(cells)
            if ("首现" in joined) and ("状态" in joined):
                header = cells
                idx = {}
                for i, c in enumerate(cells):
                    if "伏笔" in c:
                        idx.setdefault("name", i)
                    elif c.startswith("首现"):
                        idx.setdefault("first", i)
                    elif c == "状态":
                        idx["status"] = i
                    elif "预期回收" in c:
                        idx["plan"] = i
                    elif c in ("#", "＃"):
                        idx.setdefault("id", i)
                if "first" not in idx or "status" not in idx:
                    header = None
                    idx = {}
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        g = lambda k, d="": (cells[idx[k]] if k in idx and idx[k] < len(cells) else d)
        name = g("name")
        if not name:
            continue
        seq += 1
        planted = digits(g("first")) or 0
        if planted == 0:
            continue
        raw_id = g("id")
        fid = raw_id if raw_id and not raw_id.isdigit() else f"#{raw_id}" if raw_id else f"F{seq:03d}"
        st = g("status")
        status = "resolved" if "已回收" in st else "dropped" if "废弃" in st else \
            "paused" if "暂停" in st else "near" if "准备回收" in st else "open"
        items.append({"id": fid, "name": name, "planted": planted,
                      "planned": digits(g("plan")) or 0,
                      "resolved": digits(g("first")) if status == "resolved" and False else 0,
                      "status": status})
    return items


STATUS_TXT = {"resolved": "✅ 已回收", "dropped": "❌ 废弃", "paused": "⏸️ 暂停",
              "near": "🔄 准备回收", "open": "🔘 已埋设"}
GANTT_TAG = {"resolved": "done", "near": "active", "open": ""}


def main() -> int:
    ap = _ArgParser()
    ap.add_argument("--project", "-p", required=True)
    ap.add_argument("--current-ch", type=_i64, default=0)
    a = ap.parse_args()
    proj = a.project

    state = load_state(proj)
    if state is not None and state.get("foreshadow") is not None:
        items = from_state(state)
        src = SRC_NEW
    else:
        cl = os.path.join(proj, "记忆系统", "圣经", "伏笔清单.md")
        if not os.path.exists(cl):
            print("❌ 无数据源：既无 记忆系统/_state.json 也无 记忆系统/圣经/伏笔清单.md")
            return 2
        items = from_checklist(cl)
        src = SRC_OLD

    current = (a.current_ch if a.current_ch and a.current_ch > 0 else 0) or detect_current(proj)  # 负数视为未传（对齐 ps1 -le 0）
    items.sort(key=lambda x: (x["planted"], x["id"]))

    overdue = [i for i in items if i["status"] in ("open", "near") and i["planned"] and current > i["planned"]]
    pending = [i for i in items if i["status"] in ("open", "near") and i not in overdue]
    resolved = [i for i in items if i["status"] == "resolved"]
    rest = [i for i in items if i["status"] in ("dropped", "paused")]

    out = []
    out.append("# 伏笔时间线（派生件·禁手改）")
    out.append(MARK)
    out.append(f"> 数据源={src} ｜ 当前章=第{current}章 ｜ 条目 {len(items)}：待收 {len(pending)} / 过期 {len(overdue)} / 已回收 {len(resolved)} / 停废 {len(rest)}")
    out.append("")

    def table(title, rows, note_fn):
        out.append(f"## {title}")
        if not rows:
            out.append("（无）")
            out.append("")
            return
        out.append("| 线索 | 首现 | 计划回收 | 状态 | 备注 |")
        out.append("|---|---|---|---|---|")
        for i in rows:
            plan = f"第{i['planned']}章" if i["planned"] else "—"
            out.append(f"| {i['id']} {i['name']} | 第{i['planted']}章 | {plan} | {STATUS_TXT[i['status']]} | {note_fn(i)} |")
        out.append("")

    table("🚨 过期未回收（优先清旧债）", overdue,
          lambda i: f"超期 {current - i['planned']} 章" if i["planned"] else "")
    table("⏳ 待回收", pending,
          lambda i: f"剩 {i['planned'] - current} 章" if i["planned"] and i["planned"] >= current else "未定回收章")
    table("✅ 已回收", resolved, lambda i: "")
    table("⏸️ 暂停 / ❌ 废弃", rest, lambda i: "")

    out.append("## Mermaid 时间线（第 N 章 → 2000-01-01 起第 N 天）")
    out.append("```mermaid")
    out.append("gantt")
    out.append("    title 伏笔埋设与回收")
    out.append("    dateFormat YYYY-MM-DD")
    out.append("    axisFormat %Y-%m-%d")
    out.append("    section 待回收")
    for i in [x for x in overdue + pending if x["status"] in ("open", "near")]:
        title = TITLE_BAD.sub("", f"{i['id']} {i['name']} {i['planted']}-{i['planned'] or current}")
        end_ch = i["planned"] or max(current, i["planted"] + 1)
        if end_ch <= i["planted"]:
            end_ch = i["planted"] + 1
        tag = "crit" if i in overdue else GANTT_TAG[i["status"]]
        prefix = f"{title} :{tag}, " if tag else f"{title} : "
        out.append(f"    {prefix}{day_of(i['planted'])}, {day_of(end_ch)}")
    out.append("    section 已回收")
    for i in resolved:
        title = TITLE_BAD.sub("", f"{i['id']} {i['name']} {i['planted']}-{i['resolved'] or i['planned'] or i['planted']}")
        end_ch = i["resolved"] or i["planned"] or (i["planted"] + 1)
        if end_ch <= i["planted"]:
            end_ch = i["planted"] + 1
        out.append(f"    {title} :done, {day_of(i['planted'])}, {day_of(end_ch)}")
    if not resolved:
        out.append("    （无）")
    out.append("```")
    out.append("")

    target = os.path.join(proj, "记忆系统", "圣经", "伏笔时间线.md")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, target)

    print(f"【伏笔时间线】源={src} ｜ 当前章=第{current}章 ｜ 条目 {len(items)}：待收 {len(pending)} / 过期 {len(overdue)} / 已回收 {len(resolved)} / 停废 {len(rest)}")
    print("已写出：记忆系统/圣经/伏笔时间线.md")
    for i in overdue:
        print(f"🚨 过期：{i['id']} {i['name']}（第{i['planted']}章埋 → 计划第{i['planned']}章收，超期 {current - i['planned']} 章）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
