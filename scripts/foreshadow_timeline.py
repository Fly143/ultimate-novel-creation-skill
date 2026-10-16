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

MARK = "<!-- generated:foreshadow-timeline v1 | 派生件禁手改，重跑本脚本再生成 -->"
SRC_NEW, SRC_OLD = "state", "清单"
TITLE_BAD = re.compile(r"[:,：，]")


def units(text: str) -> str:
    return text


def day_of(ch: int) -> str:
    return (date(2000, 1, 1) + timedelta(days=max(ch, 1) - 1)).strftime("%Y-%m-%d")


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
            with open(st, encoding="utf-8") as f:
                best = int(json.load(f).get("last_committed_chapter", 0) or 0)
        except Exception:
            best = 0
    return best


def load_state(project: str):
    p = os.path.join(project, "记忆系统", "_state.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def from_state(state) -> list[dict]:
    items = []
    for fid, v in (state.get("foreshadow") or {}).items():
        items.append({
            "id": fid,
            "name": str(v.get("desc", "")),
            "planted": int(v.get("planted_ch", 0) or 0),
            "planned": int(v.get("planned_resolve_ch", 0) or 0),
            "resolved": int(v.get("resolved_ch") or 0),
            "status": "resolved" if v.get("resolved_ch") else "open",
        })
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
    with open(path, encoding="utf-8") as f:
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
                        idx["name"] = i
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", "-p", required=True)
    ap.add_argument("--current-ch", type=int, default=0)
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

    current = a.current_ch or detect_current(proj)
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
