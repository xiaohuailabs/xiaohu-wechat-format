#!/usr/bin/env python3
"""
主题 lint。扫 themes/*.json,规则两档:

硬规(计入退出码):
- 字号梯度倒置:h1 < h2 / h2 < h3 / h3 < h4
- 正文过小:p < 14px
- H3 小于正文:h3 < p(标题必须≥正文)

移动端警告(2026-06-12 加,计入退出码;展示型主题在 JSON 根加
"lint": {"display_type": true} 可豁免 H1/H2 上限):
- H1 > 28px(微信正文宽约 345px,29px 中文一行 11 字,长标题占 3 行)
- H2 > 22px
- 正文不在 15-17px 区间
- 正文行高 < 1.7

信息提示(不计入退出码):
- 缺 dark_mode 配置(微信深色模式走系统自动反色,满底色标题容易变脏)

三层结构主题(h2 视觉在 h2_inner 上)取 max(h2, h2_inner) 作为有效字号。

运行:
  python3 theme_lint.py               # 扫全部
  python3 theme_lint.py broadsheet    # 扫单个
  python3 theme_lint.py --quiet       # 只返回 exit code,不打印
退出码: 0=干净 / 1=有违规

gallery 前被 format.py 自动调用(非阻断,只警告)。
"""
import json
import re
import sys
from pathlib import Path

THEMES_DIR = Path(__file__).parent.parent / "themes"
MIN_P = 14
P_RANGE = (15, 17)
MAX_H1 = 28
MAX_H2 = 22
MIN_LINE_HEIGHT = 1.7
TIERS = ["h1", "h2", "h3", "h4"]


def parse_px(v):
    if not v:
        return None
    m = re.match(r"(\d+(?:\.\d+)?)\s*px", str(v))
    return float(m.group(1)) if m else None


def effective_size(styles, tag):
    """三层结构主题字号挂在 {tag}_inner 上,取两者较大值"""
    outer = parse_px(styles.get(tag, {}).get("font_size"))
    inner = parse_px(styles.get(f"{tag}_inner", {}).get("font_size"))
    candidates = [s for s in (outer, inner) if s is not None]
    return max(candidates) if candidates else None


def lint_theme(tid, data):
    styles = data.get("styles", {})
    sizes = {k: effective_size(styles, k) for k in TIERS}
    p = parse_px(styles.get("p", {}).get("font_size"))
    display_type = data.get("lint", {}).get("display_type", False)
    issues = []
    infos = []

    # 梯度倒置
    for a, b in zip(TIERS, TIERS[1:]):
        sa, sb = sizes[a], sizes[b]
        if sa is not None and sb is not None and sa < sb:
            issues.append(f"梯度倒置: {a}={sa:.0f}px < {b}={sb:.0f}px")

    # 正文过小(硬规)
    if p is not None and p < MIN_P:
        issues.append(f"正文过小: p={p:.0f}px < {MIN_P}px")

    # H3 小于正文
    h3 = sizes.get("h3")
    if h3 is not None and p is not None and h3 < p:
        issues.append(f"H3 小于正文: h3={h3:.0f}px < p={p:.0f}px")

    # ── 移动端规则 ──
    if not display_type:
        if sizes.get("h1") is not None and sizes["h1"] > MAX_H1:
            issues.append(f"H1 过大: {sizes['h1']:.0f}px > {MAX_H1}px(手机占多行)")
        if sizes.get("h2") is not None and sizes["h2"] > MAX_H2:
            issues.append(f"H2 过大: {sizes['h2']:.0f}px > {MAX_H2}px(手机占两行)")

    if p is not None and not (P_RANGE[0] <= p <= P_RANGE[1]):
        issues.append(f"正文字号越界: p={p:.0f}px(最佳 {P_RANGE[0]}-{P_RANGE[1]}px)")

    lh_raw = styles.get("p", {}).get("line_height")
    try:
        lh = float(lh_raw) if lh_raw is not None else None
    except (TypeError, ValueError):
        lh = None
    if lh is not None and lh < MIN_LINE_HEIGHT:
        issues.append(f"正文行高过紧: {lh}(应 ≥{MIN_LINE_HEIGHT})")

    # ── 信息提示 ──
    if "dark_mode" not in data:
        infos.append("缺 dark_mode 配置(深色模式走系统自动反色)")

    return issues, infos


def main():
    quiet = "--quiet" in sys.argv
    targets = [a for a in sys.argv[1:] if not a.startswith("--")]

    theme_files = (
        [THEMES_DIR / f"{t}.json" for t in targets]
        if targets
        else sorted(THEMES_DIR.glob("*.json"))
    )

    total_bad = 0
    total_info = 0
    bad_themes = []
    for fp in theme_files:
        if not fp.exists():
            if not quiet:
                print(f"[skip] {fp.name} 不存在")
            continue
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        issues, infos = lint_theme(fp.stem, data)
        total_info += len(infos)
        if issues:
            total_bad += len(issues)
            bad_themes.append(fp.stem)
            if not quiet:
                print(f"\n[{fp.stem}]")
                for i in issues:
                    print(f"  ⚠ {i}")
                for i in infos:
                    print(f"  ℹ {i}")

    if not quiet:
        print()
        if total_bad == 0:
            extra = f"(另有 {total_info} 条 dark_mode 缺失提示)" if total_info else ""
            print(f"✓ {len(theme_files)} 个主题全部通过 {extra}")
        else:
            print(f"⚠ {len(bad_themes)} 个主题共 {total_bad} 处违规: {', '.join(bad_themes)}")

    return 0 if total_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
