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
import math
import re
import sys
from pathlib import Path

BUDGET_PATH = Path(__file__).resolve().parent / "doc-budget.json"
# 度量口径（与 ps1 逐字一致）：UTF-8 字节解码剥 BOM → 换行归一化为 \n → 移除空白集合
WS_RE = re.compile("[\t\n\x0b\x0c\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+")  # 全空白集（对齐 _WS_REF 与 ps1）：原窄集漏 CR / x1c-x1f / x85 / u2000-u200a 等
_WS_REF = "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
_I64_MIN, _I64_MAX = -(2 ** 63), 2 ** 63 - 1


def _in_i64(v) -> bool:
    # 与 ps1 的 [int]/[long] 判型对齐：超 Int64 的整数 ps1 判非法 → py 同判（fail-closed）
    return isinstance(v, int) and not isinstance(v, bool) and _I64_MIN <= v <= _I64_MAX
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def read_raw(root: Path, rel: str) -> str:
    text = (root / rel).read_bytes().decode("utf-8-sig", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def load_rules() -> dict:
    if not BUDGET_PATH.is_file():
        print(f"❌ 缺少规则单源：{BUDGET_PATH}")
        raise SystemExit(1)
    try:
        with BUDGET_PATH.open(encoding="utf-8-sig") as f:
            rules = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"❌ 规则单源解析失败：{BUDGET_PATH}")
        print(f"解析详情：{exc}", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(rules, dict):
        print(f"❌ 规则单源解析失败：{BUDGET_PATH}（须为 JSON 对象）")
        raise SystemExit(1)
    return rules


def validate_rules(rules: dict) -> list[str]:
    """必填键 schema 校验（与 check-doc-budget.ps1 同清单同文案，fail-closed）。"""
    errs: list[str] = []
    if not isinstance(rules.get("files"), list):
        errs.append("files 须为数组")
    if not isinstance(rules.get("paths"), list):
        errs.append("paths 须为数组")
    if not isinstance(rules.get("path_ceiling"), dict):
        errs.append("path_ceiling 须为对象")
    files = rules.get("files")
    if isinstance(files, list):
        for i, item in enumerate(files):
            if not isinstance(item, dict) or "path" not in item or "budget" not in item:
                errs.append(f"files[{i}] 须为含 path/budget 的对象")
                continue
            if not isinstance(item["path"], str):
                errs.append(f"files[{i}].path 须为字符串")
            # 严格整数：float(10.9) 截断与 .NET 四舍五入会让双引擎按不同阈值裁决 → 必须类型判定
            if not _in_i64(item["budget"]):
                errs.append(f"files[{i}].budget 须为整数")
    paths = rules.get("paths")
    if isinstance(paths, list):
        _seenLbl = set()
        for i, item in enumerate(paths):
            if not isinstance(item, dict) or "label" not in item or "files" not in item:
                errs.append(f"paths[{i}] 须为含 label/files 的对象")
                continue
            if item["label"] in _seenLbl:
                errs.append(f"paths[{i}] label 重复：{item['label']}（重复行会静默双计/歧义豁免）")  # label 唯一性 fail-closed（原重复不校验，同路径重复打印一行）
                continue
            _seenLbl.add(item["label"])
            for key in ("files", "branches", "stacking"):
                if key not in item:
                    continue
                v = item[key]
                if isinstance(v, str):
                    continue
                if isinstance(v, list) and all(
                    isinstance(e, str)
                    or (key != "files" and isinstance(e, list) and e and all(isinstance(x, str) for x in e))
                    for e in v
                ):
                    continue
                if key == "files":
                    errs.append(f"paths[{i}].files 须为字符串或字符串数组")
                else:
                    errs.append(f"paths[{i}].{key} 组须为字符串或非空字符串数组")
    gen = rules.get("generated")
    if gen is not None and not (
        isinstance(gen, str) or (isinstance(gen, list) and all(isinstance(e, str) for e in gen))
    ):
        errs.append("generated 须为字符串或字符串数组")
    ceiling = rules.get("path_ceiling") if isinstance(rules.get("path_ceiling"), dict) else {}
    ex = ceiling.get("exempt")
    if ex is not None and not (
        isinstance(ex, str) or (isinstance(ex, list) and all(isinstance(e, str) for e in ex))
    ):
        errs.append("exempt 须为字符串或字符串数组")
    lim = ceiling.get("limit")
    if not _in_i64(lim) or lim <= 0:
        errs.append("path_ceiling.limit 须为正整数（0 会使路径上限闸短路 fail-open）")
    return errs


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


def _ref_list(refs) -> list:
    """refs 归一为列表：单字符串简写 → 单元素（与 ps1 `@($Refs)` 同口径，禁止 list(str) 拆字符）。"""
    if refs is None:
        return []
    if isinstance(refs, str):
        return [refs]
    return list(refs)


def measure(root: Path, ref: str) -> tuple[int | None, str]:
    """ref = 'path' 或 'path#标题' → (字符数, 状态)。"""
    if ref is None or not str(ref).strip():
        return None, "空引用"
    rel, _, frag = ref.partition("#")
    rel = rel.strip()
    if not rel:
        return None, f"空引用：{ref}"
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


def sum_refs(root: Path, refs) -> tuple[int, list[str]]:
    total = 0
    problems: list[str] = []
    for ref in _ref_list(refs):
        if ref is None or not str(ref).strip():
            continue  # 空引用跳过（与 ps1 Sum-Refs 同口径）
        n, status = measure(root, ref)
        if n is None:
            problems.append(status)
        else:
            total += n
    return total, problems


class _ArgParser(argparse.ArgumentParser):
    """退出码契约仅 0/1（与 check-done.py 同款）：用法错误也走 1。"""

    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"❌ 参数错误：{message}", file=sys.stderr)
        raise SystemExit(1)


def main() -> int:
    ap = _ArgParser(description="热路径文档预算守卫")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]), help="仓库根")
    ap.add_argument("--print-baseline", action="store_true", help="额外打印基线汇总")
    args = ap.parse_args()
    root = Path(args.root).expanduser().resolve()
    if not BUDGET_PATH.is_file():
        print(f"❌ 缺少规则单源：{BUDGET_PATH}")
        return 1
    rules = load_rules()
    schema_errs = validate_rules(rules)
    if schema_errs:
        print(f"❌ 规则单源校验失败：{BUDGET_PATH}（{'；'.join(schema_errs)}）")
        return 1
    errors: list[str] = []

    print(f"【check-doc-budget】root={root}")
    print(f"规则={BUDGET_PATH.name} 度量=去空白字符")

    # 文件级
    print("\n文件预算：")
    for item in rules.get("files", []):
        ref, budget = item["path"], int(item["budget"])
        n, status = measure(root, ref)
        if n is None:
            errors.append(f"文件校验：{status}")
            print(f"  ❌ {ref} — {status}")
            continue
        if n > budget:
            _w = item.get("why", "")
            _w = _w if isinstance(_w, str) else ""
            errors.append(f"超预算：{ref} {n:,} > {budget:,}（{_w}）")
            print(f"  ❌ {ref} {n:,}/{budget:,} 超出 {n - budget:,} 字符")
        elif budget > 0 and (budget - n) * 100 < budget * 5:
            _pct = math.floor((budget - n) * 1000.0 / budget) / 10.0  # 向下取整到 0.1%，避免「5.0%<5%」矛盾
            errors.append(f"余量<5%：{ref} {n:,}/{budget:,}（余 {budget - n:,}，{_pct:.1f}%）——删等量旧文本或扩预算写明 why（禁止静默放宽）")
            print(f"  ❌ {ref} {n:,}/{budget:,} 余 {budget - n:,}（{_pct:.1f}%<5%）")
        else:
            print(f"  ✅ {ref} {n:,}/{budget:,}（余 {budget - n:,}）")

    # 上下文分层（P3-18）：合法层名单单源 = context_layers 的键（排除 'why' 元字段）
    layers_doc = rules.get("context_layers") or {}
    valid_layers = [k for k in layers_doc if k != "why"] or ["protected", "compressible"]
    lc = {k: 0 for k in valid_layers}
    for item in rules.get("files", []):
        layer = str(item.get("layer", ""))
        if layer in lc:
            lc[layer] += 1
        else:
            errors.append(f"分层缺失/非法：{item.get('path','?')} layer={layer}")
    _path_totals: dict = {}
    _max_single: tuple = ("", -1)
    for _item_m in rules.get("files", []):
        _ref_m = _item_m.get("path")
        if not isinstance(_ref_m, str):
            continue
        _n_m, _st_m = measure(root, _ref_m)
        if _n_m is not None and _n_m > _max_single[1]:
            _max_single = (_ref_m.split('#')[0].strip(_WS_REF), _n_m)
    _lparts = " / ".join(f"{k} {lc[k]}" for k in valid_layers)
    print(f"分层校验：{_lparts}（压缩时只裁 compressible，protected 永不裁）")

    # 生成型条目（键缺失/null 与 ps1 同口径：不输出）
    for g in _ref_list(rules.get("generated")):
        if g is None:
            continue
        print(f"  ⚙ generated（脚本产出，不手测）：{g if isinstance(g, str) else json.dumps(g, ensure_ascii=False)}")

    # 路径级
    ceiling = rules.get("path_ceiling", {}) or {}
    limit = int(ceiling.get("limit", 0))
    exempt = set(_ref_list(ceiling.get("exempt", [])))
    print(f"\n路径合计（上限 path_ceiling.limit={limit:,}）：")
    worst = 0
    worst_label = ""
    for path in rules.get("paths", []):
        label = path["label"]
        total, problems = sum_refs(root, path.get("files", []))
        detail = [f"base={total:,}"]
        branch_totals = []
        for branch in _ref_list(path.get("branches", []) or []):
            # branches = 多组互斥分支，每组是一串 ref（组内求和）；组间取最大值（与 ps1 Sum-Refs 同语义）
            # 字符串简写（branches="x.md" / 组内单串）由 _ref_list 归一，禁止 list(str) 拆字符
            b, probs = sum_refs(root, branch)
            errors.extend(f"{label} 分支：{p}" for p in probs)
            branch_totals.append(b)
        if branch_totals:
            total += max(branch_totals)
            detail.append(f"+branch(max)={max(branch_totals):,}")
        for grp in _ref_list(path.get("stacking", []) or []):
            s, probs = sum_refs(root, grp)
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
            _path_totals[label] = total
            if total > worst:
                worst, worst_label = total, label
        print(f"  {flag} {label} {total:,}/{limit:,}（{' '.join(detail)}）")

    def _bl_disp(v):
        # 非法基线值展示归一（与 ps1 0.##########/ConvertTo-Json 口径对齐）
        if v is None:
            return ""
        if isinstance(v, bool):
            return str(v)
        if isinstance(v, float):
            if v == 0:
                return "0"  # ±0.0 统一显示 0（ps5.1 G15 丢负号，与 py/pwsh7 对齐）
            if v != v:
                return "NAN"
            if v == float("inf"):
                return "INFINITY"
            if v == float("-inf"):
                return "-INFINITY"
            return f"{v:.15g}".upper()
        if isinstance(v, int) and not _in_i64(v):
            # 超 Int64 整数（ps1 pwsh7=BigInteger / 5.1=Double）→ 统一 G15 展示
            return f"{float(v):.15g}".upper()
        if isinstance(v, (list, dict)):
            return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        return str(v)

    # 基线防漂移闸：baseline 五字段须与实测一致（改文档后必须回填，禁止静默过期）；缺键/换型一律 fail-closed
    _bl = rules.get("baseline")
    if not isinstance(_bl, dict):
        errors.append("基线非法：baseline 块缺失或非对象（须含 worst/writer/resident/precheck/max_single 五字段）")
    else:
        for _k_miss in ("worst_path_chars", "writer_delegated_chars", "session_resident_chars", "precheck_path_chars", "max_single_file"):
            if _k_miss not in _bl:
                errors.append(f"基线缺键：baseline.{_k_miss}（五字段必须齐全）")
        if "worst_path_chars" in _bl:
            _w_b = _bl["worst_path_chars"]
            if not _in_i64(_w_b):
                _wbd = _bl_disp(_w_b)
                errors.append(f"基线非法：baseline.worst_path_chars={_wbd}（应为整数，非整型值会绕过过期闸——fail-closed）")
            elif _w_b != worst:
                errors.append(f"基线过期：baseline.worst_path_chars={_w_b:,} ≠ 实测 {worst:,}（改文档后回填 doc-budget.json baseline）")
        for _lbl_b, _key_b in (("写一章·写手委派", "writer_delegated_chars"), ("主会话常驻（入口+协议+索引）", "session_resident_chars"), ("写前预检", "precheck_path_chars")):
            _v_b = _bl.get(_key_b)
            if _key_b in _bl and not _in_i64(_v_b):
                _vbd = _bl_disp(_v_b)
                errors.append(f"基线非法：baseline.{_key_b}={_vbd}（应为整数，非整型值会绕过过期闸——fail-closed）")
            elif _v_b is not None and _lbl_b in _path_totals and _v_b != _path_totals[_lbl_b]:
                errors.append(f"基线过期：baseline.{_key_b}={_v_b:,} ≠ 实测 {_path_totals[_lbl_b]:,}（改文档后回填 doc-budget.json baseline）")
        _msf_b = _bl.get("max_single_file")
        if "max_single_file" in _bl:
            if not isinstance(_msf_b, str) or "=" not in _msf_b:
                errors.append(f"基线非法：baseline.max_single_file={_bl_disp(_msf_b)}（应为 path=整数）")
            else:
                _sp_b, _sn_b = _msf_b.rsplit("=", 1)
                if not re.fullmatch(r"-?[0-9]+", _sn_b):
                    errors.append(f"基线非法：baseline.max_single_file={_msf_b}（应为 path=整数）")
                elif not _in_i64(int(_sn_b)):
                    errors.append(f"基线非法：baseline.max_single_file={_msf_b}（应为 path=整数）")
                elif _sp_b != _max_single[0] or int(_sn_b) != _max_single[1]:
                    errors.append(f"基线过期：baseline.max_single_file={_msf_b} ≠ 实测 {_max_single[0]}={_max_single[1]:,}（路径段须为实测最大单文件名；改文档后回填 doc-budget.json baseline）")
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
