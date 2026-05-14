#!/usr/bin/env python3
"""
SGF 解析器 —— 围棋小说写作专用

功能：
  1. 读取 SGF 文件，解析主分支（实战）的所有着法
  2. 输出手数表：手数 | 颜色 | SGF坐标 | 位置描述 | 注释(如有)
  3. 支持坐标校验：给定 SGF 文件和手数，返回该手的真实坐标

用法：
  python sgf_parser.py <sgf_path>                    # 输出完整手数表
  python sgf_parser.py <sgf_path> --moves 20-30      # 只输出 20-30 手
  python sgf_parser.py <sgf_path> --check "W[qk]=26" # 校验第26手是否是 W[qk]
  python sgf_parser.py <sgf_path> --json              # JSON 格式输出
"""

import re
import sys
import json
import os

# ── SGF 坐标 → 中文位置描述 ──────────────────────────────────────────

STAR_POINTS = {
    "dd": "左上角星位", "pd": "右上角星位",
    "dp": "左下角星位", "pp": "右下角星位",
    "jd": "左边星位",  "pj": "上边星位",
    "jp": "右边星位",  "dj": "下边星位",
    "jj": "天元",
}

# 常见定式位置
COMMON_POSITIONS = {
    "cd": "左上小目", "dc": "左上小目",
    "qd": "右上小目", "pq": "右上小目",
    "cp": "左下小目", "pc": "左下小目",
    "qq": "右下小目",
    "ce": "左上高目", "ec": "左上高目",
    "qe": "右上高目", "eq": "右上高目",
    "cq": "左下高目", "qc": "左下高目",
    "pe": "右下高目", "ep": "右下高目",
    "cf": "左上目外", "fc": "左上目外",
    "qf": "右上目外", "fq": "右上目外",
    "pf": "右下目外", "fp": "右下目外",
    "df": "左下目外", "fd": "左下目外",
}


def sgf_to_gtp(coord):
    """SGF 坐标 (如 pd) → GTP 坐标 (如 Q16)"""
    if len(coord) != 2:
        return coord
    col = ord(coord[0]) - ord('a') + 1
    row = ord(coord[1]) - ord('a') + 1
    col_letter = chr(ord('A') + col - 1)
    if col_letter >= 'I':
        col_letter = chr(ord(col_letter) + 1)
    return f"{col_letter}{row}"


def coord_to_desc(coord):
    """SGF 坐标 → 中文位置描述"""
    coord = coord.lower()
    if len(coord) != 2:
        return f"({coord})"

    if coord in STAR_POINTS:
        return STAR_POINTS[coord]
    if coord in COMMON_POSITIONS:
        return COMMON_POSITIONS[coord]

    col = ord(coord[0]) - ord('a') + 1
    row = ord(coord[1]) - ord('a') + 1

    # 方位
    lr = "左" if col <= 6 else ("右" if col >= 14 else "中")
    ud = "上" if row >= 14 else ("下" if row <= 6 else "")

    # 第几线
    dist = min(col, 20 - col, row, 20 - row)
    line = ""
    if dist <= 3:
        line = ["", "一路", "二路", "三路"][dist]

    # 组合
    if ud == "" and line:
        return f"{lr}中{line}"
    if not line:
        return f"{lr}上方" if ud == "上" else f"{lr}下方"
    return f"{lr}{ud}{line}"


# ── SGF 解析器 ───────────────────────────────────────────────────────

class SGFParser:
    """解析 SGF 文件，提取实战主分支所有着法"""

    def __init__(self, content):
        self.raw = content
        self.header = {}     # PB, PW, RE, DT, etc.
        self.moves = []      # [(color, coord, comment)]

        self._parse_header()
        self.moves = self._extract_main_line()

    def _parse_header(self):
        """解析 SGF 头部信息"""
        root_end = self.raw.find(';')
        if root_end == -1:
            return
        root_section = self.raw[:root_end]
        for key in ['PB', 'PW', 'RE', 'DT', 'KM', 'BR', 'WR', 'GN', 'EV', 'PC']:
            m = re.search(rf'{re.escape(key)}\[([^\]]*)\]', root_section)
            if m:
                self.header[key] = m.group(1)

    def _extract_main_line(self):
        """
        提取主分支（实战）所有着法。
        策略：通过 SGF 节点树结构追踪，在每个分支点只选第一个子节点。
        """
        content = re.sub(r'\s+', '', self.raw)

        moves = []
        pos = 0
        # 跳过根节点属性（直到找到根节点的子节点）
        # 根节点以 ( 开头，属性在第一个 ; 之前
        first_semi = content.find(';')
        if first_semi == -1:
            return moves

        # 从第一个 ; 开始解析
        pos = first_semi
        in_bracket = False

        while pos < len(content):
            c = content[pos]

            # 处理转义
            if c == '\\' and in_bracket:
                pos += 2
                continue

            # 处理括号
            if c == '[':
                in_bracket = True
                pos += 1
                continue
            elif c == ']':
                in_bracket = False
                pos += 1
                continue

            if in_bracket:
                pos += 1
                continue

            # 在括号外
            if c == ';':
                # 读节点内容直到遇到 ; ( ) 之一（在根括号外）
                node_start = pos + 1
                node_end = self._read_node_text(content, node_start)
                node_text = content[node_start:node_end]

                # 提取着法
                bm = re.search(r'B\[([a-s][a-s])\]', node_text)
                wm = re.search(r'W\[([a-s][a-s])\]', node_text)
                cm = re.search(r'C\[([^\]]*)\]', node_text)
                comment = cm.group(1) if cm else ""

                if bm:
                    moves.append(('B', bm.group(1), comment))
                elif wm:
                    moves.append(('W', wm.group(1), comment))

                pos = node_end
                continue

            elif c == '(':
                # 变招开始，我们的策略：第一个子分支是主分支
                # 递归进入，读完后跳过同级其他分支
                inner_moves, inner_end = self._read_first_branch(content, pos + 1)
                moves.extend(inner_moves)

                # 跳过此层级的所有剩余分支
                depth = 1
                j = inner_end
                while j < len(content) and depth > 0:
                    cc = content[j]
                    if cc == '[':
                        j = self._skip_bracket(content, j)
                        continue
                    if cc == '(':
                        depth += 1
                    elif cc == ')':
                        depth -= 1
                    elif cc == '\\':
                        j += 1
                    j += 1
                pos = j
                continue

            elif c == ')':
                # 不应该在这里遇到——应该是递归处理的
                break

            else:
                pos += 1

        return moves

    def _read_node_text(self, content, start):
        """从 start 位置读取节点文本（直到遇到 ; ( ) 之一）"""
        i = start
        depth = 0
        in_bracket = False
        while i < len(content):
            c = content[i]
            if c == '\\':
                i += 2
                continue
            if c == '[':
                in_bracket = True
            elif c == ']':
                in_bracket = False
            elif not in_bracket:
                if c in '();' and depth == 0:
                    return i
                if c == '(':
                    depth += 1
                elif c == ')':
                    depth -= 1
            i += 1
        return i

    def _skip_bracket(self, content, start):
        """跳过 [...] 内容"""
        i = start + 1
        while i < len(content):
            if content[i] == '\\':
                i += 2
                continue
            if content[i] == ']':
                return i + 1
            i += 1
        return i + 1

    def _read_first_branch(self, content, start):
        """
        读取第一个分支（变招子树）的所有着法。
        返回：(moves_list, end_position)
        """
        moves = []
        pos = start
        in_bracket = False

        while pos < len(content):
            c = content[pos]
            if c == '\\':
                pos += 2
                continue
            if c == '[':
                in_bracket = True
                pos += 1
                continue
            elif c == ']':
                in_bracket = False
                pos += 1
                continue

            if in_bracket:
                pos += 1
                continue

            if c == ')':
                # 本分支结束
                return moves, pos + 1

            elif c == ';':
                node_start = pos + 1
                node_end = self._read_node_text(content, node_start)
                node_text = content[node_start:node_end]

                bm = re.search(r'B\[([a-s][a-s])\]', node_text)
                wm = re.search(r'W\[([a-s][a-s])\]', node_text)
                cm = re.search(r'C\[([^\]]*)\]', node_text)
                comment = cm.group(1) if cm else ""

                if bm:
                    moves.append(('B', bm.group(1), comment))
                elif wm:
                    moves.append(('W', wm.group(1), comment))

                pos = node_end
                continue

            elif c == '(':
                # 子变招 - 只进第一个
                inner_moves, inner_end = self._read_first_branch(content, pos + 1)
                moves.extend(inner_moves)

                # 跳过同级其他分支
                depth = 1
                j = inner_end
                while j < len(content) and depth > 0:
                    cc = content[j]
                    if cc == '[':
                        j = self._skip_bracket(content, j)
                        continue
                    if cc == '(':
                        depth += 1
                    elif cc == ')':
                        depth -= 1
                    elif cc == '\\':
                        j += 1
                    j += 1
                pos = j
                continue

            else:
                pos += 1

        return moves, pos

    # ── 输出方法 ─────────────────────────────────────────────────

    def get_move_table(self, start=1, end=None):
        """获取结构化的手数表"""
        result = []
        for i, (color, coord, comment) in enumerate(self.moves, 1):
            if i < start:
                continue
            if end is not None and i > end:
                break
            color_cn = "黑" if color == "B" else "白"
            desc = coord_to_desc(coord)
            gtp = sgf_to_gtp(coord)
            comment_short = comment[:60].replace('\r', ' ').replace('\n', ' ') if comment else ""
            result.append({
                'num': i,
                'color': color_cn,
                'coord': coord.upper(),
                'gtp': gtp,
                'desc': desc,
                'comment': comment_short
            })
        return result

    def format_table(self, start=1, end=None):
        """格式化为可读表格"""
        rows = self.get_move_table(start, end)
        if not rows:
            return "⚠️ 未解析到任何着法（SGF 格式不兼容？）"

        # 头部信息
        pb = self.header.get('PB', '?')
        pw = self.header.get('PW', '?')
        re_str = self.header.get('RE', '?')
        dt = self.header.get('DT', '?')
        lines = [
            f"📋 {pb} vs {pw}",
            f"   结果：{re_str}  日期：{dt}",
            f"   总手数：{len(self.moves)}  显示：{start}~{end or len(self.moves)}",
            "",
            f"{'手数':>4} | {'色':2} | {'坐标':5} | {'位置描述':16} | {'注释'}",
            "-" * 90,
        ]

        for r in rows:
            comment = r['comment'][:50] if r['comment'] else ""
            lines.append(f"{r['num']:>4} | {r['color']}  | {r['coord']:<4} | {r['desc']:<14} | {comment}")

        return "\n".join(lines)

    def format_json(self, start=1, end=None):
        rows = self.get_move_table(start, end)
        return json.dumps({
            'header': self.header,
            'total_moves': len(self.moves),
            'moves': rows
        }, ensure_ascii=False, indent=2)

    def verify_move(self, claimed_coord, move_number):
        """
        校验声称的着法是否与 SGF 一致。
        claimed_coord: 如 "qk" 或 "QK" 或 "W[qk]"
        move_number: 手数 (1-based)
        返回: (passed, actual_coord, message)
        """
        claimed = claimed_coord.lower().strip()
        claimed = re.sub(r'[bw\[\]]', '', claimed)

        if move_number < 1 or move_number > len(self.moves):
            return False, None, f"手数 {move_number} 超出范围 (1-{len(self.moves)})"

        _, actual, _ = self.moves[move_number - 1]

        if claimed == actual:
            return True, actual, f"第{move_number}手={claimed.upper()} ✅ 正确"
        else:
            actual_desc = coord_to_desc(actual)
            return False, actual, f"第{move_number}手 声称={claimed.upper()} ❌ 实际 SGF={actual.upper()}({actual_desc})"


def main():
    if len(sys.argv) < 2:
        print("用法：python sgf_parser.py <sgf_path> [--moves 10-30] [--check 'W[qk]=26'] [--json]")
        sys.exit(1)

    sgf_path = sys.argv[1]
    if not os.path.exists(sgf_path):
        print(f"❌ 文件不存在：{sgf_path}")
        sys.exit(1)

    with open(sgf_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    parser = SGFParser(content)

    # 参数解析
    start, end = 1, None
    check = None
    use_json = False

    for i, arg in enumerate(sys.argv[2:], 2):
        if arg == '--json':
            use_json = True
        elif arg.startswith('--moves='):
            parts = arg.split('=')[1].split('-')
            start = int(parts[0])
            end = int(parts[1]) if len(parts) > 1 else None
        elif arg.startswith('--check='):
            check = arg.split('=', 1)[1]

    # ── 单个校验模式 ──
    if check:
        m = re.match(r'(?:[bw]\[)?([a-s][a-s])\]?\s*=\s*(\d+)', check, re.I)
        if m:
            claimed_coord = m.group(1)
            move_num = int(m.group(2))
            passed, actual, msg = parser.verify_move(claimed_coord, move_num)
            result = {
                'claimed': claimed_coord.upper(),
                'move_number': move_num,
                'actual': actual.upper() if actual else None,
                'passed': passed,
                'message': msg
            }
            if use_json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                status = "✅" if passed else "❌"
                print(f"{status} {msg}")
            sys.exit(0 if passed else 1)
        else:
            print(f"❌ --check 参数格式错误：{check}（正确格式：W[qk]=26 或 qk=26）")
            sys.exit(1)

    # ── 批量校验模式 ──
    if '--check-list' in sys.argv:
        idx = sys.argv.index('--check-list')
        list_file = sys.argv[idx + 1]
        if not os.path.exists(list_file):
            print(f"❌ 校验列表文件不存在：{list_file}")
            sys.exit(1)

        all_passed = True
        checks = []
        with open(list_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                # 格式：坐标=手数 或 手数=坐标
                m1 = re.match(r'([bw]\[)?([a-s][a-s])\]?\s*=\s*(\d+)', line, re.I)
                m2 = re.match(r'(\d+)\s*=\s*([bw]\[)?([a-s][a-s])\]?', line, re.I)
                m = m1 or m2
                if m:
                    if m1:
                        c, n = m1.group(2), int(m1.group(3))
                    else:
                        c, n = m2.group(3), int(m2.group(2))
                    passed, actual, msg = parser.verify_move(c, n)
                    checks.append({
                        'claimed': c.upper(),
                        'move_number': n,
                        'actual': actual.upper() if actual else None,
                        'passed': passed,
                        'message': msg
                    })
                    if not passed:
                        all_passed = False

        total = len(checks)
        passed_count = sum(1 for c in checks if c['passed'])

        if use_json:
            print(json.dumps({
                'total': total, 'passed': passed_count, 'checks': checks
            }, ensure_ascii=False, indent=2))
        else:
            print(f"\n📊 校验统计：{passed_count}/{total} 通过")
            for c in checks:
                s = "✅" if c['passed'] else "❌"
                print(f"  {s} {c['message']}")

        sys.exit(0 if all_passed else 1)

    # ── 输出模式 ──
    if use_json:
        print(parser.format_json(start, end))
    else:
        print(parser.format_table(start, end))
        print(f"\n💡 更多：`--moves=10-30` 截取  |  `--check 'W[qk]=26'` 校验  |  `--json` JSON输出")


if __name__ == '__main__':
    main()
