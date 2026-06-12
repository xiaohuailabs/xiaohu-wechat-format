#!/usr/bin/env python3
"""中文正文半角→全角标点修复器 / 质检器。

规则:
- 标点两侧至少一侧是中文字符(CJK)时,半角标点换全角
- 保护区:代码块 ```...```、行内 code `...`、URL、Markdown 链接 url 段

用法:
    zh_punctuation_fix.py <input.md>            # dry run,打印 diff
    zh_punctuation_fix.py <input.md> --write    # 写回原文件
    zh_punctuation_fix.py <input.md> --check    # 只扫描,发现违规退出码 1

⚠️ 坑:replacement 字符串里的全角标点必须用 chr(0xff0c) 等显式 codepoint,
    不能直接打字符——写代码时 IME 很可能切成半角,肉眼看不出,静默失败。
"""

import re
import sys
import argparse

CJK = r'[\u4e00-\u9fff]'

# 全角字符(显式用 codepoint,防止 IME 误切)
FW_COMMA = chr(0xff0c)   # ,
FW_COLON = chr(0xff1a)   # :
FW_SEMI = chr(0xff1b)    # ;
FW_QMARK = chr(0xff1f)   # ?
FW_EXCL = chr(0xff01)    # !
FW_PERIOD = chr(0x3002)  # 。
FW_LPAREN = chr(0xff08)  # (
FW_RPAREN = chr(0xff09)  # )

PROTECT_PATTERNS = [
    r'```[\s\S]*?```',
    r'`[^`\n]+?`',
    r'https?://\S+',
    r'\]\([^)\n]+\)',
]


def protect(text):
    bag = []
    def sub(m):
        bag.append(m.group(0))
        return f'\x00P{len(bag)-1}\x00'
    for pat in PROTECT_PATTERNS:
        text = re.sub(pat, sub, text)
    return text, bag


def restore(text, bag):
    for i, s in enumerate(bag):
        text = text.replace(f'\x00P{i}\x00', s)
    return text


def fix(text):
    text, bag = protect(text)

    # 左邻中文 + 半角标点 → 全角
    text = re.sub(f'({CJK})[ \\t]*,', lambda m: m.group(1) + FW_COMMA, text)
    text = re.sub(f'({CJK})[ \\t]*:', lambda m: m.group(1) + FW_COLON, text)
    text = re.sub(f'({CJK})[ \\t]*;', lambda m: m.group(1) + FW_SEMI, text)
    text = re.sub(f'({CJK})[ \\t]*\\?', lambda m: m.group(1) + FW_QMARK, text)
    text = re.sub(f'({CJK})[ \\t]*!', lambda m: m.group(1) + FW_EXCL, text)
    # 半角句点:中文 + . 后接空白或中文或行尾
    text = re.sub(
        f'({CJK})[ \\t]*\\.(?=[\\s\\u4e00-\\u9fff]|$)',
        lambda m: m.group(1) + FW_PERIOD,
        text,
        flags=re.MULTILINE,
    )
    # 左括号:左邻中文 → 全角
    text = re.sub(f'({CJK})[ \\t]*\\(', lambda m: m.group(1) + FW_LPAREN, text)
    # 右括号:右邻中文或中文标点或行尾 → 全角
    text = re.sub(
        r'\)(?=[\u4e00-\u9fff' + FW_COMMA + FW_PERIOD + FW_SEMI + FW_COLON + FW_EXCL + FW_QMARK + r'、\s]|$)',
        FW_RPAREN,
        text,
        flags=re.MULTILINE,
    )

    return restore(text, bag)


def count_violations(text):
    text, _ = protect(text)
    cnt = 0
    patterns = [
        f'{CJK}[ \\t]*,',
        f'{CJK}[ \\t]*:',
        f'{CJK}[ \\t]*;',
        f'{CJK}[ \\t]*\\?',
        f'{CJK}[ \\t]*!',
        f'{CJK}[ \\t]*\\(',
        rf'\)[ \t]*{CJK}',
    ]
    for p in patterns:
        cnt += len(re.findall(p, text))
    return cnt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()

    with open(args.path, encoding='utf-8') as f:
        orig = f.read()

    if args.check:
        n = count_violations(orig)
        print(f'[punctuation-check] {args.path}: {n} 处中文正文混用半角标点')
        sys.exit(1 if n > 0 else 0)

    fixed = fix(orig)
    if fixed == orig:
        print(f'[punctuation-fix] {args.path}: 无需修复')
        sys.exit(0)

    before = count_violations(orig)
    after = count_violations(fixed)
    print(f'[punctuation-fix] 违规: {before} -> {after}')

    if args.write:
        with open(args.path, 'w', encoding='utf-8') as f:
            f.write(fixed)
        print(f'[punctuation-fix] 已写回 {args.path}')
    else:
        import difflib
        diff = list(difflib.unified_diff(
            orig.splitlines(), fixed.splitlines(), lineterm='', n=1
        ))[:40]
        for line in diff:
            print(line)
        print('\n(dry run,加 --write 写回)')


if __name__ == '__main__':
    main()
