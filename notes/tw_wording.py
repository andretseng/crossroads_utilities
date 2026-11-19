#!/usr/bin/env python3
"""tw_wording.py — Taiwan wording check (D11, item 8).

`TW_WORDS` (same order as translate_answer.py) and `convert(text) -> str` (fenced code blocks and
inline code untouched); CLI `tw_wording.py FILE` prints converted text; `tw_wording.py --check FILE`
prints `line: word -> replacement` for every hit outside code and exits 1 if any.
"""
from __future__ import annotations

import re
import sys

# Taiwan wording, by the program. Only words with one meaning in this kind of text; code blocks and
# `inline code` are left as they are. Same order as translate_answer.py.
TW_WORDS = [("下降沿", "下降緣"), ("上升沿", "上升緣"), ("邊沿", "邊緣"), ("幀", "訊框"), ("函數", "函式"),
            ("定時器", "計時器"), ("捕獲", "擷取"), ("超時", "逾時"), ("採樣", "取樣"), ("寄存器", "暫存器"),
            ("軟件", "軟體"), ("硬件", "硬體"), ("內存", "記憶體"), ("數據", "資料"), ("信號", "訊號"),
            ("默認", "預設"), ("調用", "呼叫"), ("運行", "執行"), ("指針", "指標"), ("變量", "變數"),
            ("字節", "位元組"), ("比特", "位元"), ("模塊", "模組"), ("接口", "介面"), ("端口", "連接埠"),
            ("服務器", "伺服器"), ("線程", "執行緒"), ("隊列", "佇列"), ("堆棧", "堆疊"), ("支持", "支援"),
            ("設置", "設定"), ("優化", "最佳化"), ("源代碼", "原始碼"), ("代碼", "程式碼"), ("宏", "巨集")]

# A fenced code block (```...```) or inline code (`...`). Split on these so replacements skip them.
_CODE_SPLIT = re.compile(r"(```.*?```|`[^`\n]*`)", re.S)


def convert(text: str) -> str:
    """Replace Taiwan wording outside fenced code blocks and inline code."""
    out = []
    for part in re.split(r"(```.*?```|`[^`\n]*`)", text, flags=re.S):
        if not part.startswith("`"):
            for word, replacement in TW_WORDS:
                part = part.replace(word, replacement)
        out.append(part)
    return "".join(out)


def check(text: str) -> list[str]:
    """Return ``"line: word -> replacement"`` for every hit outside code, in line order."""
    hits: list[str] = []
    # Track which lines are inside a fenced code block.
    in_fence = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for word, replacement in TW_WORDS:
            if word in line:
                hits.append(f"{line_no(text, line)}: {word} -> {replacement}")
    return hits


def line_no(text: str, line: str) -> int:
    """1-based line number of the first occurrence of ``line`` in ``text``."""
    idx = text.index(line)
    return text.count("\n", 0, idx) + 1


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print("usage: tw_wording.py [--check] FILE", file=sys.stderr)
        return 2
    check_mode = False
    if argv[0] == "--check":
        check_mode = True
        argv = argv[1:]
    if not argv:
        print("usage: tw_wording.py [--check] FILE", file=sys.stderr)
        return 2
    path = argv[0]
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if check_mode:
        hits = check(text)
        for hit in hits:
            print(hit)
        return 1 if hits else 0
    sys.stdout.write(convert(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
