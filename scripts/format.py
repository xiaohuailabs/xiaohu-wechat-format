#!/usr/bin/env python3
"""微信公众号文章排版工具

将 Markdown 文件转为微信公众号兼容的内联样式 HTML。
微信编辑器不支持 <style> 标签、CSS class 和 JS，
所以所有样式必须用 style="..." 内联写在每个标签上。

用法:
    python3 format.py --input article.md --theme elegant [--vault-root /path] [--output /path]
    python3 format.py --input article.md --format plain  # 纯 HTML 输出（无微信兼容处理）
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import markdown

# ── 脚注占位符（UUID 防冲突）──────────────────────────────────────────
_FN_PREFIX = f"__FN_{uuid.uuid4().hex[:8]}_"
FOOTNOTE_PLACEHOLDERS = {
    "footnote_sup": f"{_FN_PREFIX}SUP__",
    "footnote_section": f"{_FN_PREFIX}SECTION__",
    "footnote_title": f"{_FN_PREFIX}TITLE__",
    "footnote_item": f"{_FN_PREFIX}ITEM__",
}

# ── Callout 类型颜色映射（语义色，不依赖主题）──────────────────────────
CALLOUT_TYPE_COLORS = {
    "tip":       {"border": "#10b981", "bg": "rgba(16,185,129,0.06)", "icon": "\U0001f4a1"},
    "note":      {"border": "#3b82f6", "bg": "rgba(59,130,246,0.06)", "icon": "\U0001f4dd"},
    "important": {"border": "#8b5cf6", "bg": "rgba(139,92,246,0.06)", "icon": "\u26a1"},
    "warning":   {"border": "#f59e0b", "bg": "rgba(245,158,11,0.06)", "icon": "\u26a0\ufe0f"},
    "caution":   {"border": "#ef4444", "bg": "rgba(239,68,68,0.06)",  "icon": "\U0001f534"},
    "callout":   None,  # 默认用主题色，不覆盖
}

# Gallery 核心主题列表（按用途分类，不存在的会跳过）
GALLERY_THEMES = [
    # 2026-06-12 合并去重：59→34，砍掉 25 款"同骨架换色"的重复变体（文件保留在 themes/，仅移出 gallery）
    # 新主题候选（2026-06-12 新做：数据简报/圆桌访谈/极简文档/琉璃）
    "data-report", "interview", "notion-doc", "glass-light",
    # 纸系·Kami（衬线骨架，保留代表色，原 3→1）
    "kami-ink",
    # 新做精选（各骨架代表，原 14→8）
    "claude-scroll", "swiss-grid", "pastel-dream", "brutalism-raw",
    "blueprint", "academic-paper", "industrial", "magazine-serif",
    # 特色布局（hero 16 个色变体合并为 3 个代表：靛紫/深海暗/禅极简，原 17→4）
    "hero-purple", "dark-ocean", "timeline-green", "zen-minimal",
    # 卡片系列（原 5→3）
    "warm-card", "apple-gradient", "cyber-neon",
    # 深度长文（4，均独特骨架）
    "newspaper", "magazine", "ink", "coffee-house",
    # 科技产品（4，均独特骨架）
    "bytedance", "github", "sspai", "midnight",
    # 文艺随笔（原 4→2）
    "terracotta", "mint-fresh",
    # 活力动态（4，均独特骨架）
    "sports", "bauhaus", "chinese", "wechat-native",
    # 模板布局（4，均独特骨架）
    "minimal-gold", "focus-blue", "elegant-green", "bold-blue",
]

# Gallery 示例文章（写死，不用用户文章）
GALLERY_DEMO_MARKDOWN = """\
## 主要功能

在数字化时代，**内容创作**变得越来越重要。一款好的排版工具，能让你的文章在众多内容中**脱颖而出**。

> 好的排版不只是视觉享受，更是对读者的尊重。

### 核心亮点

- 完整的 Markdown 语法支持
- 精美的主题样式
- 一键复制到微信发布

1. 撰写你的内容
2. 选择喜欢的风格
3. 一键复制粘贴

---

### 代码示例

`inline code` 也是支持的。

```python
def hello():
    print("Hello, World!")
```

| 功能 | 状态 |
|------|------|
| 实时预览 | 已支持 |
| 主题选择 | 已支持 |

> [!tip] 小技巧
> 选择适合你文章风格的主题，效果更佳。
"""

# ── 路径 ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent
THEMES_DIR = SKILL_DIR / "themes"
TEMPLATE_DIR = SKILL_DIR / "templates"

with open(SKILL_DIR / "config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)

OUTPUT_DIR = Path(CONFIG["output_dir"])
VAULT_ROOT = Path(CONFIG["vault_root"])
DEFAULT_THEME = CONFIG["settings"]["default_theme"]
AUTO_OPEN = CONFIG["settings"]["auto_open_browser"]
# 卡片/时间线/hero 布局标题区的作者署名，可在 config.json 的
# settings.header_author_label 单独配置（与 wechat.author 草稿作者字段解耦）
HEADER_AUTHOR = (
    CONFIG.get("settings", {}).get("header_author_label")
    or CONFIG.get("wechat", {}).get("author", "")
)


# ── 主题加载 ────────────────────────────────────────────────────────────
def load_theme(theme_name: str) -> dict:
    """加载主题。支持三种格式：
    1. 传统主题名: 'terracotta' → themes/terracotta.json
    2. 矩阵组合名: 'accent-ocean' → layouts/accent.json + palettes/ocean.json 合并
    3. 如果都找不到，报错
    """
    # 1. 先尝试传统主题
    theme_path = THEMES_DIR / f"{theme_name}.json"
    if theme_path.exists():
        with open(theme_path, encoding="utf-8") as f:
            return json.load(f)

    # 2. 尝试矩阵组合
    if "-" in theme_name:
        layout_name, palette_name = theme_name.split("-", 1)
        layout_path = THEMES_DIR / "layouts" / f"{layout_name}.json"
        palette_path = THEMES_DIR / "palettes" / f"{palette_name}.json"
        if layout_path.exists() and palette_path.exists():
            return merge_layout_palette(layout_path, palette_path)

    # 3. 报错
    available = [p.stem for p in THEMES_DIR.glob("*.json")]
    # 加上矩阵组合
    layouts = [p.stem for p in (THEMES_DIR / "layouts").glob("*.json")] if (THEMES_DIR / "layouts").exists() else []
    palettes = [p.stem for p in (THEMES_DIR / "palettes").glob("*.json")] if (THEMES_DIR / "palettes").exists() else []
    if layouts and palettes:
        available.append(f"矩阵组合: {','.join(layouts)} × {','.join(palettes)}")
    print(f"错误: 主题 '{theme_name}' 不存在。\n可用: {', '.join(available)}")
    sys.exit(1)


def merge_layout_palette(layout_path: Path, palette_path: Path) -> dict:
    """合并布局模板和色板，替换占位符"""
    with open(layout_path, encoding="utf-8") as f:
        layout = json.load(f)
    with open(palette_path, encoding="utf-8") as f:
        palette = json.load(f)

    # 构建替换映射
    replacements = {
        "{{accent}}": palette["accent"],
        "{{accent_light}}": palette["accent_light"],
        "{{primary}}": palette["primary"],
        "{{background}}": palette["background"],
        "{{blockquote_bg}}": palette["blockquote_bg"],
        "{{code_bg}}": palette["code_bg"],
        "{{hr_color}}": palette["hr_color"],
        "{{footnote_bg}}": palette["footnote_bg"],
        "{{table_border}}": palette["table_border"],
        "{{dark_accent}}": palette["dark_accent"],
    }

    # 计算派生色
    accent_hex = palette["accent"]
    accent_light_hex = palette["accent_light"]
    # 10% 透明度
    replacements["{{accent_10}}"] = f"rgba({int(accent_hex[1:3],16)},{int(accent_hex[3:5],16)},{int(accent_hex[5:7],16)},0.1)"
    # 30% 透明度
    replacements["{{accent_light_30}}"] = f"rgba({int(accent_light_hex[1:3],16)},{int(accent_light_hex[3:5],16)},{int(accent_light_hex[5:7],16)},0.3)"

    # 把整个 layout JSON 转成字符串，做全局替换，再转回
    layout_str = json.dumps(layout, ensure_ascii=False)
    for placeholder, value in replacements.items():
        layout_str = layout_str.replace(placeholder, value)

    result = json.loads(layout_str)
    result["name"] = f"{layout['name']} · {palette['name']}"
    result["description"] = f"{layout['name']}布局 + {palette['name']}配色"

    # 补充 colors 字段（某些地方可能读取）
    result["colors"] = {
        "primary": palette["primary"],
        "accent": palette["accent"],
        "background": palette["background"],
        "blockquote_bg": palette["blockquote_bg"],
        "code_bg": palette["code_bg"],
        "hr_color": palette["hr_color"],
        "footnote_bg": palette["footnote_bg"],
    }

    return result


# ── 工具函数 ────────────────────────────────────────────────────────────
def count_words(text: str) -> int:
    """统计中文文章字数（中文字符 + 英文单词）"""
    clean = re.sub(r"[#*`\[\]()!>|{}_~\-]", "", text)
    clean = re.sub(r"\n+", "\n", clean)
    chinese = len(re.findall(r"[\u4e00-\u9fff]", clean))
    english = len(re.findall(r"[a-zA-Z]+", clean))
    return chinese + english


def extract_title(content: str, filepath: Path) -> str:
    """从内容或文件名提取标题"""
    # 从 frontmatter 提取
    fm = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if fm:
        for line in fm.group(1).split("\n"):
            if line.startswith("title:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    # 从 H1 提取
    h1 = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if h1:
        return h1.group(1).strip()
    # 从文件名提取
    name = filepath.stem
    name = re.sub(r"^\d{4}-\d{2}-\d{2}-?", "", name)
    name = re.sub(r"-(公众号|小红书|微博)$", "", name)
    return name or filepath.stem


def strip_frontmatter(content: str) -> str:
    """去掉 YAML frontmatter"""
    return re.sub(r"^---\n.*?\n---\n*", "", content, flags=re.DOTALL)


def fix_cjk_spacing(text: str) -> str:
    """中英文/中数字之间自动加空格（跳过代码块、行内代码、URL、链接）"""
    lines = text.split("\n")
    result = []
    in_code_block = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            continue
        if in_code_block:
            result.append(line)
            continue

        # 保护不应修改的片段
        protected = []
        def _protect(m):
            protected.append(m.group(0))
            return f"\x00P{len(protected)-1}\x00"

        line = re.sub(r"`[^`]+`", _protect, line)            # 行内代码
        line = re.sub(r"https?://\S+", _protect, line)       # URL
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", _protect, line)  # 图片
        line = re.sub(r"\[[^\]]*\]\([^)]*\)", _protect, line)   # 链接

        cjk = r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]"
        latin = r"[a-zA-Z0-9]"
        line = re.sub(f"({cjk})({latin})", r"\1 \2", line)
        line = re.sub(f"({latin})({cjk})", r"\1 \2", line)

        for i, p in enumerate(protected):
            line = line.replace(f"\x00P{i}\x00", p)
        result.append(line)

    return "\n".join(result)


def fix_cjk_bold_punctuation(text: str) -> str:
    """把中文标点移到加粗/斜体标记外面

    **文字，** → **文字**，
    *文字。*  → *文字*。
    """
    cjk_punct = r"[，。！？、；：""''（）【】《》…—]"
    # **text+标点** → **text**+标点
    text = re.sub(rf"\*\*([^*]+?)({cjk_punct}+)\*\*", r"**\1**\2", text)
    # *text+标点* → *text*+标点（不匹配 **）
    text = re.sub(rf"(?<!\*)\*(?!\*)([^*]+?)({cjk_punct}+)\*(?!\*)", r"*\1*\2", text)
    return text


def _localize_image(img_path: Path, images_dir: Path) -> str:
    """把图片落到输出目录，返回最终文件名。

    .svg 自动转 PNG（qlmanage）——公众号素材库不收 SVG，直接传会失败。
    转换失败时退回复制原文件并打警告。
    """
    images_dir.mkdir(parents=True, exist_ok=True)
    if img_path.suffix.lower() == ".svg":
        png_name = img_path.stem + ".png"
        dest = images_dir / png_name
        if dest.exists():
            return png_name
        try:
            subprocess.run(
                ["qlmanage", "-t", "-s", "1600", "-o", str(images_dir), str(img_path)],
                capture_output=True, timeout=30,
            )
            ql_out = images_dir / f"{img_path.name}.png"
            if ql_out.exists():
                ql_out.rename(dest)
                print(f"[SVG] {img_path.name} → {png_name}（公众号不支持 SVG 图片，已自动转 PNG）")
                return png_name
        except Exception:
            pass
        print(f"[SVG] ⚠ {img_path.name} 转 PNG 失败，已按原样复制——公众号后台无法上传 SVG，请手动转换")
    dest = images_dir / img_path.name
    if not dest.exists():
        shutil.copy2(img_path, dest)
    return img_path.name


def convert_wikilinks(text: str, vault_root: Path, output_dir: Path) -> str:
    """把 Obsidian ![[image.jpg]] 转为 <img> 标签，复制图片到输出目录"""
    images_dir = output_dir / "images"
    # 搜索路径：vault 目录（如需额外图片目录，在 config.json 的 image_search_paths 中配置）
    search_roots = [vault_root]
    # 支持自定义图片搜索目录
    config_path = SKILL_DIR / "config.json"
    if config_path.exists():
        import json as _json
        try:
            _cfg = _json.load(open(config_path, encoding="utf-8"))
            for p in _cfg.get("image_search_paths", []):
                search_roots.append(Path(p).expanduser())
        except Exception:
            pass

    def replace_img(match):
        filename = match.group(1).strip()
        # 处理带尺寸的 wikilink: ![[image.jpg|300]]
        if "|" in filename:
            filename = filename.split("|")[0].strip()
        # 在多个目录中搜索图片（followlinks=True 跟随符号链接）
        for search_root in search_roots:
            if not search_root.exists():
                continue
            for root, dirs, files in os.walk(search_root, followlinks=True):
                if filename in files:
                    img_path = Path(root) / filename
                    final_name = _localize_image(img_path, images_dir)
                    # 返回占位标记，后面注入样式时处理
                    return f'<section data-role="img-wrapper"><img src="images/{final_name}" alt="{final_name}"></section>'
        return f'<span style="color:#999;">[图片: {filename}]</span>'

    return re.sub(r"!\[\[([^\]]+)\]\]", replace_img, text)


def copy_markdown_images(text: str, input_dir: Path, output_dir: Path) -> str:
    """处理标准 Markdown 图片 ![alt](path)，把本地相对路径图片复制到输出目录"""
    images_dir = output_dir / "images"

    def replace_md_img(match):
        alt = match.group(1)
        src = match.group(2).strip()
        # 跳过外链（http/https）；外链 SVG 公众号转存会失败，提前警告
        if src.startswith(("http://", "https://")):
            if src.split("?")[0].lower().endswith(".svg"):
                print(f"[SVG] ⚠ 外链 SVG 图片公众号无法转存，请换 PNG/JPG: {src}")
            return match.group(0)
        # 解析相对路径，基于输入文件所在目录
        img_path = (input_dir / src).resolve()
        if img_path.exists():
            final_name = _localize_image(img_path, images_dir)
            # 统一改为 images/filename 路径
            return f'![{alt}](images/{final_name})'
        return match.group(0)

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_md_img, text)


def extract_links_as_footnotes(html: str) -> tuple[str, str]:
    """提取外部链接转为脚注格式

    返回: (处理后的 HTML, 脚注 HTML)
    """
    footnotes = []
    counter = [0]
    ph_sup = FOOTNOTE_PLACEHOLDERS["footnote_sup"]
    ph_section = FOOTNOTE_PLACEHOLDERS["footnote_section"]
    ph_title = FOOTNOTE_PLACEHOLDERS["footnote_title"]
    ph_item = FOOTNOTE_PLACEHOLDERS["footnote_item"]

    def replace_link(match):
        full = match.group(0)
        href = match.group(1)
        text = match.group(2)

        # 跳过锚点链接和非 http 链接
        if not href.startswith("http"):
            return full

        counter[0] += 1
        idx = counter[0]
        footnotes.append((idx, text, href))
        # 正文中加上标注
        return f'{text}<sup style="{ph_sup}">[{idx}]</sup>'

    processed = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', replace_link, html)

    if not footnotes:
        return processed, ""

    # 生成脚注区
    fn_html = f'<section style="{ph_section}">\n'
    fn_html += f'<p style="{ph_title}">参考链接</p>\n'
    for idx, text, href in footnotes:
        fn_html += f'<p style="{ph_item}">[{idx}] {text}: {href}</p>\n'
    fn_html += "</section>"

    return processed, fn_html


_SMART_SYSTEM_PROMPT = """你是一个公众号排版预处理器。分析 Markdown 文章，在合适位置添加排版语义标记。

可用标记（只能用这些）：
1. `> [!important] 内容` — 核心判断高亮框，每节最多1个，全文最多3个
2. `:::stat\n数字\n说明文字\n:::` — 数据亮点大字展示，全文最多2个
3. `:::byline[作者名]\n内容\n:::` — 作者总结段落，只在文末使用
4. `> — 作者名` — 引用块末尾的出处标注
5. 保持 `**术语：** 解释` 格式的并列段落（3个以上连续时有效）

规则：
- 不改任何原文文字，只在原文周围添加标记
- 如果文章已有 `> [!important]` 或 `> [!tip]` 等 callout，不再添加新的
- 如果段落以 `**小互说：**` 开头且在文末，转成 `:::byline[小互说]`
- 总标记数不超过 5 个/2000字
- 输出完整的修改后 Markdown，不要输出解释"""


def smart_enhance_markdown(content: str, config_path: Path) -> str:
    """调用 AI 分析文章并注入语义标记。需要 config.json 中配置 smart_api。"""
    import urllib.request

    # 读取配置
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    smart_cfg = cfg.get("settings", {}).get("smart_api") or cfg.get("smart_api")
    if not smart_cfg:
        print("错误: --smart 需要在 config.json 中配置 smart_api")
        print('示例: { "smart_api": { "base_url": "https://api.openai.com/v1", "api_key": "sk-...", "model": "gpt-4o-mini" } }')
        sys.exit(1)

    base_url = smart_cfg["base_url"].rstrip("/")
    api_key = smart_cfg["api_key"]
    model = smart_cfg.get("model", "gpt-4o-mini")

    # 预检：已有足够标记则跳过
    existing = (
        content.count("> [!important]") + content.count("> [!tip]") +
        content.count(":::stat") + content.count(":::byline")
    )
    if existing >= 3:
        print(f"  文章已有 {existing} 个排版标记，跳过 AI 分析")
        return content

    print("  AI 语义分析中...")

    # 构建请求
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _SMART_SYSTEM_PROMPT},
            {"role": "user", "content": content}
        ],
        "temperature": 0.3,
    }).encode("utf-8")

    url = f"{base_url}/chat/completions"
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        enhanced = result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  AI 分析失败: {e}，使用原文")
        return content

    # 后验：检查 AI 是否改了原文文字（只允许加标记，不允许改内容）
    # 简单检查：去掉所有标记后，剩余文字应与原文一致
    def strip_markers(text):
        text = re.sub(r'^> \[!(?:important|tip|warning|caution|note)\]\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'^:::(?:stat|byline)(?:\[.*?\])?\s*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'^:::\s*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'^> —\s*.+$', '', text, flags=re.MULTILINE)
        return re.sub(r'\n{3,}', '\n\n', text).strip()

    original_text = strip_markers(content)
    enhanced_text = strip_markers(enhanced)

    # 允许小幅差异（AI 可能调整空行），用字符级相似度
    if len(enhanced_text) < len(original_text) * 0.9:
        print("  AI 输出内容偏差过大，使用原文")
        return content

    # 统计添加了多少标记
    new_markers = (
        enhanced.count("> [!important]") - content.count("> [!important]") +
        enhanced.count(":::stat") - content.count(":::stat") +
        enhanced.count(":::byline") - content.count(":::byline")
    )
    print(f"  AI 添加了 {new_markers} 个排版标记")
    return enhanced


def _auto_detect_byline(content: str) -> str:
    """自动检测文末的 **小互说：** 或 ## 小互说，转为 :::byline 容器。"""
    if ':::byline' in content:
        return content  # 已有容器，不重复转

    # Pattern 1: ## 小互说 标题
    match = re.search(r'^## 小互说\s*\n+(.+?)(?=\n## |\Z)', content, re.MULTILINE | re.DOTALL)
    if match and match.start() > len(content) * 0.6:
        byline_text = match.group(1).strip()
        return content[:match.start()] + f':::byline[小互说]\n{byline_text}\n:::\n' + content[match.end():]

    # Pattern 2: **小互说：** 内联
    match = re.search(r'^\*\*小互说[：:]\*\*\s*(.+?)(?=\n\n|\Z)', content, re.MULTILINE | re.DOTALL)
    if match and match.start() > len(content) * 0.6:
        byline_text = match.group(1).strip()
        return content[:match.start()] + f':::byline[小互说]\n{byline_text}\n:::\n' + content[match.end():]

    return content


def process_callouts(text: str) -> str:
    """处理 Obsidian callout 语法: > [!callout] 内容"""
    lines = text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        # 检查是否是 callout 开始
        callout_match = re.match(r"^>\s*\[!([\w]+)\]\s*(.*)", lines[i])
        if callout_match:
            callout_type = callout_match.group(1)
            title = callout_match.group(2).strip()
            content_lines = []
            i += 1
            # 收集 callout 内容行
            while i < len(lines) and lines[i].startswith(">"):
                content_lines.append(lines[i][1:].strip())
                i += 1
            content = "\n".join(content_lines)
            # 用特殊标记包裹
            if title:
                result.append(f'<div class="callout" data-type="{callout_type}">')
                result.append(f'<p class="callout-title">{title}</p>')
            else:
                result.append(f'<div class="callout" data-type="{callout_type}">')
            result.append(f'<p class="callout-content">{content}</p>')
            result.append("</div>")
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


def process_manual_footnotes(text: str) -> str:
    """处理手写脚注 [^N] 语法

    1. 找出所有 [^N]: 内容 定义行，收集并删除
    2. 把正文中的 [^N] 替换为上标
    3. 在文末追加脚注区 HTML
    """
    ph_sup = FOOTNOTE_PLACEHOLDERS["footnote_sup"]
    ph_section = FOOTNOTE_PLACEHOLDERS["footnote_section"]
    ph_title = FOOTNOTE_PLACEHOLDERS["footnote_title"]
    ph_item = FOOTNOTE_PLACEHOLDERS["footnote_item"]

    # 1. 提取并删除脚注定义行
    footnote_defs = {}
    def collect_def(match):
        idx = match.group(1)
        content = match.group(2).strip()
        footnote_defs[idx] = content
        return ""  # 删除该行

    text = re.sub(r"^\[\^(\d+)\]:\s*(.+)$", collect_def, text, flags=re.MULTILINE)

    if not footnote_defs:
        return text

    # 清理可能残留的空行
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 2. 替换正文中的 [^N] 为上标
    def replace_ref(match):
        idx = match.group(1)
        return f'<sup class="manual-footnote" style="{ph_sup}">[{idx}]</sup>'

    text = re.sub(r"\[\^(\d+)\]", replace_ref, text)

    # 3. 生成脚注区 HTML
    fn_html = f'\n<section style="{ph_section}">\n'
    fn_html += f'<p style="{ph_title}">注释</p>\n'
    for idx in sorted(footnote_defs.keys(), key=int):
        fn_html += f'<p style="{ph_item}">[{idx}] {footnote_defs[idx]}</p>\n'
    fn_html += "</section>\n"

    text = text.rstrip() + "\n" + fn_html

    return text


def process_fenced_containers(text: str, auto_video: bool = True) -> str:
    """处理 :::type[title] ... ::: 围栏容器语法（支持嵌套）

    支持的容器：
    - :::dialogue[标题] — 对话气泡
    - :::gallery[标题]  — 横向滚动图片画廊
    - :::longimage[标题] — 长图滚动展示
    - :::stat — 关键数字展示
    - :::timeline[标题] — 时间线
    - :::steps[标题] — 步骤流程
    - :::compare[A vs B] — 对比卡片
    - :::quote[人名] — 人物引言
    - :::video[标题] — 视频卡片（公众号不能嵌外链视频，渲染为封面卡+链接脚注）
    - :::intro — 文首导读块
    - :::end[可选CTA文案] — END 结束符
    - :::history[往期回顾] — 文末往期文章卡
    """
    # 先把裸视频链接自动包成 :::video 容器（仅顶层执行，递归时关闭防止无限套娃）
    if auto_video:
        text = _auto_video_cards(text)
        # 金句卡片：>> / >>> 嵌套引用在源码层转占位容器
        # （python-markdown 会把相邻引用块合并成一棵树，HTML 层无法可靠区分层级）
        text = _quote_cards_md(text)

    container_re = re.compile(
        r"^:::(dialogue|gallery|longimage|stat|timeline|steps|compare|quote|byline|video|intro|end|history)"
        r"(?:\[([^\]]*)\])?\s*$"
    )

    lines = text.split("\n")
    result = []
    i = 0
    while i < len(lines):
        container_match = container_re.match(lines[i])
        if container_match:
            container_type = container_match.group(1)
            container_title = (container_match.group(2) or "").strip()
            content_lines = []
            i += 1
            # 收集容器内容直到遇到匹配的 :::（支持嵌套计数）
            depth = 1
            while i < len(lines) and depth > 0:
                if container_re.match(lines[i]):
                    depth += 1
                    content_lines.append(lines[i])
                elif lines[i].strip() == ":::":
                    depth -= 1
                    if depth > 0:
                        content_lines.append(lines[i])
                else:
                    content_lines.append(lines[i])
                i += 1

            # 递归处理内部容器
            inner_text = "\n".join(content_lines)
            inner_text = process_fenced_containers(inner_text, auto_video=False)
            inner_lines = inner_text.split("\n")

            if container_type == "dialogue":
                result.append(_build_dialogue_html(container_title, inner_lines))
            elif container_type == "gallery":
                # 先转 markdown 再包裹
                inner_html = md_to_html(inner_text)
                result.append(
                    f'<section data-container="gallery">'
                    f'<p data-container="gallery-title">{container_title}</p>'
                    f'<section data-container="gallery-scroll">'
                    f'{inner_html}'
                    f'</section></section>'
                )
            elif container_type == "longimage":
                inner_html = md_to_html(inner_text)
                result.append(
                    f'<section data-container="longimage">'
                    f'<p data-container="longimage-title">{container_title}</p>'
                    f'<section data-container="longimage-scroll">'
                    f'{inner_html}'
                    f'</section></section>'
                )
            elif container_type == "stat":
                result.append(_build_stat_html(inner_lines))
            elif container_type == "timeline":
                result.append(_build_timeline_html(container_title, inner_lines))
            elif container_type == "steps":
                result.append(_build_steps_html(container_title, inner_lines))
            elif container_type == "compare":
                result.append(_build_compare_html(container_title, inner_lines))
            elif container_type == "quote":
                result.append(_build_quote_html(container_title, inner_lines))
            elif container_type == "byline":
                result.append(_build_byline_html(container_title, inner_lines))
            elif container_type == "video":
                result.append(_build_video_html(container_title, inner_lines))
            elif container_type == "intro":
                result.append(_build_intro_html(container_title, inner_lines))
            elif container_type == "end":
                result.append(_build_end_html(container_title))
            elif container_type == "history":
                result.append(_build_history_html(container_title, inner_lines))
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


# 裸视频链接识别：YouTube / B站 / 视频号 / 直链视频文件，独占一行时自动转视频卡
_VIDEO_URL_RE = re.compile(
    r"^\s*(?:!\[[^\]]*\]\(\s*)?"
    r"(https?://(?:www\.)?(?:youtube\.com/watch\S*|youtu\.be/\S+|bilibili\.com/video/\S+|"
    r"b23\.tv/\S+|weixin\.qq\.com/sph/\S+)|\S+\.(?:mp4|mov|webm)(?:\?\S*)?)"
    r"\)?\s*$",
    re.IGNORECASE,
)


def _auto_video_cards(text: str) -> str:
    """独占一行的视频链接（含 ![](x.mp4) 形式）自动包成 :::video 容器，跳过代码块"""
    lines = text.split("\n")
    out = []
    in_fence = False
    converted = 0
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        m = None if in_fence else _VIDEO_URL_RE.match(line)
        if m and ":::" not in line:
            url = m.group(1)
            out.append(f":::video[视频]\n{url}\n:::")
            converted += 1
        else:
            out.append(line)
    if converted:
        print(f"[视频] 检测到 {converted} 个视频链接 → 已转为视频卡片（公众号不支持外链视频，"
              f"正文显示封面卡+脚注链接；如需播放器请在后台手动插入视频号/腾讯视频）")
    return "\n".join(out)


def _quote_cards_md(text: str) -> str:
    """markdown 源码层把 >> 块转阴影卡、>>> 块转居中金句卡（mdnice multiquote 同款）"""
    lines = text.split("\n")
    out = []
    in_fence = False
    i = 0
    re3 = re.compile(r"^\s*>\s*>\s*>\s*(.*)$")
    re2 = re.compile(r"^\s*>\s*>\s*(.*)$")
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        m3 = None if in_fence else re3.match(line)
        m2 = None if (in_fence or m3) else re2.match(line)
        if m3 or m2:
            triple = bool(m3)
            pat = re3 if triple else re2
            buf = []
            while i < len(lines):
                cur = lines[i]
                if not triple and re3.match(cur):
                    break
                mm = pat.match(cur)
                if mm is None:
                    break
                if mm.group(1).strip():
                    buf.append(mm.group(1).strip())
                i += 1
            container = "quote-card-center" if triple else "quote-card"
            paras = "".join(f'<p data-container="quote-card-p">{t}</p>' for t in buf)
            out.append("")
            out.append(f'<section data-container="{container}">{paras}</section>')
            out.append("")
        else:
            out.append(line)
            i += 1
    return "\n".join(out)


def _build_video_html(title: str, lines: list[str]) -> str:
    """视频卡片：▶ 徽章 + 标题 + 链接（链接会被脚注系统接管）"""
    non_empty = [l.strip() for l in lines if l.strip()]
    url = non_empty[0] if non_empty else ""
    # 第二行起可写说明文字
    desc = non_empty[1] if len(non_empty) > 1 else ""
    title = title or "视频"
    desc_html = f'<p data-container="video-desc">{desc}</p>' if desc else ""
    return (
        f'<section data-container="video">'
        f'<section data-container="video-badge">▶</section>'
        f'<section data-container="video-info">'
        f'<p data-container="video-title">{title}</p>'
        f'{desc_html}'
        f'<p data-container="video-link"><a href="{url}">观看视频</a></p>'
        f'</section></section>'
    )


def _build_intro_html(title: str, lines: list[str]) -> str:
    """文首导读块（科技号报道头式）"""
    content = "<br>".join(l.strip() for l in lines if l.strip())
    label = title or "导读"
    return (
        f'<section data-container="intro">'
        f'<p data-container="intro-label">{label}</p>'
        f'<p data-container="intro-content">{content}</p>'
        f'</section>'
    )


def _build_end_html(cta: str) -> str:
    """END 结束符：— END — + 可选 CTA 文案"""
    cta_html = f'<p data-container="end-cta">{cta}</p>' if cta else ""
    return (
        f'<section data-container="end">'
        f'<section data-container="end-row">'
        f'<section data-container="end-line"></section>'
        f'<p data-container="end-text">END</p>'
        f'<section data-container="end-line"></section>'
        f'</section>'
        f'{cta_html}'
        f'</section>'
    )


def _build_history_html(title: str, lines: list[str]) -> str:
    """往期回顾卡：内容为 [标题](链接) 列表，链接交给脚注系统"""
    items = []
    link_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    for line in lines:
        m = link_re.search(line)
        if m:
            items.append(f'<p data-container="history-item">· <a href="{m.group(2)}">{m.group(1)}</a></p>')
        elif line.strip().lstrip("-").strip():
            items.append(f'<p data-container="history-item">· {line.strip().lstrip("-").strip()}</p>')
    return (
        f'<section data-container="history">'
        f'<p data-container="history-title">{title or "往期回顾"}</p>'
        f'{"".join(items)}'
        f'</section>'
    )


def _build_stat_html(lines: list[str]) -> str:
    """构建关键数字容器 HTML，支持单数据和多数据（value | label）"""
    non_empty = [l.strip() for l in lines if l.strip()]

    # 检测多数据格式：value | label
    multi_stats = []
    for line in non_empty:
        if '|' in line:
            parts = line.split('|', 1)
            multi_stats.append((parts[0].strip(), parts[1].strip()))

    if len(multi_stats) >= 2:
        html = '<section data-container="stat-row">'
        for value, label in multi_stats:
            html += (
                f'<section data-container="stat-item">'
                f'<p data-container="stat-number">{value}</p>'
                f'<p data-container="stat-label">{label}</p>'
                f'</section>'
            )
        html += '</section>'
        return html

    # 单数据
    number = non_empty[0] if len(non_empty) > 0 else ""
    label = non_empty[1] if len(non_empty) > 1 else ""
    return (
        f'<section data-container="stat">'
        f'<p data-container="stat-number">{number}</p>'
        f'<p data-container="stat-label">{label}</p>'
        f'</section>'
    )


def _build_byline_html(author: str, lines: list[str]) -> str:
    """构建作者总结容器 HTML（如"小互说"）"""
    content = "<br>".join(l.strip() for l in lines if l.strip())
    return (
        f'<section data-container="byline">'
        f'<p data-container="byline-author">{author}</p>'
        f'<p data-container="byline-content">{content}</p>'
        f'</section>'
    )


def _build_timeline_html(title: str, lines: list[str]) -> str:
    """构建时间线容器 HTML"""
    html = '<section data-container="timeline">'
    if title:
        html += f'<p data-container="timeline-title">{title}</p>'
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 匹配 时间: 内容 或 时间：内容
        m = re.match(r"^(.+?)\s*[：:]\s*(.+)$", line)
        if m:
            time_text = m.group(1).strip()
            content = m.group(2).strip()
            html += (
                f'<section data-container="timeline-item">'
                f'<span data-container="timeline-time">{time_text}</span>'
                f'<span data-container="timeline-dot">\u25cf</span>'
                f'<span data-container="timeline-content">{content}</span>'
                f'</section>'
            )
    html += '</section>'
    return html


def _build_steps_html(title: str, lines: list[str]) -> str:
    """构建步骤流程容器 HTML"""
    html = '<section data-container="steps">'
    if title:
        html += f'<p data-container="steps-title">{title}</p>'
    step_num = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        step_num += 1
        html += (
            f'<section data-container="steps-item">'
            f'<span data-container="steps-number">{step_num}</span>'
            f'<span data-container="steps-content">{line}</span>'
            f'</section>'
        )
    html += '</section>'
    return html


def _build_compare_html(title: str, lines: list[str]) -> str:
    """构建对比卡片容器 HTML

    标题格式: A vs B，提取 A 和 B 作为两列表头
    每行格式: 左内容 | 右内容
    """
    # 从标题中提取两列名
    left_name = ""
    right_name = ""
    if " vs " in title:
        parts = title.split(" vs ", 1)
        left_name = parts[0].strip()
        right_name = parts[1].strip()
    elif " VS " in title:
        parts = title.split(" VS ", 1)
        left_name = parts[0].strip()
        right_name = parts[1].strip()

    html = '<section data-container="compare">'
    # 表头
    if left_name or right_name:
        html += (
            f'<section data-container="compare-header">'
            f'<span data-container="compare-header-left">{left_name}</span>'
            f'<span data-container="compare-header-right">{right_name}</span>'
            f'</section>'
        )
    # 内容行
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            parts = line.split("|", 1)
            left = parts[0].strip()
            right = parts[1].strip()
        else:
            left = line
            right = ""
        html += (
            f'<section data-container="compare-row">'
            f'<span data-container="compare-left">{left}</span>'
            f'<span data-container="compare-right">{right}</span>'
            f'</section>'
        )
    html += '</section>'
    return html


def _build_quote_html(author: str, lines: list[str]) -> str:
    """构建人物引言容器 HTML"""
    # 用 <br> 连接多行内容
    content_html = "<br>".join(l.strip() for l in lines if l.strip())
    return (
        f'<section data-container="quote-card">'
        f'<p data-container="quote-mark">\u275d</p>'
        f'<p data-container="quote-text">{content_html}</p>'
        f'<p data-container="quote-author">\u2014 {author}</p>'
        f'</section>'
    )


def _build_dialogue_html(title: str, lines: list[str]) -> str:
    """将对话行解析为结构化 HTML

    每行格式: 说话人: 内容 或 说话人：内容（中英文冒号都支持）
    """
    bubbles = []
    speakers_seen = []  # 记录出现顺序，用于左右判断

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 匹配 说话人: 内容 或 说话人：内容
        m = re.match(r"^(.+?)\s*[：:]\s*(.+)$", line)
        if m:
            speaker = m.group(1).strip()
            text = m.group(2).strip()
            if speaker not in speakers_seen:
                speakers_seen.append(speaker)
            # 根据说话人在列表中的索引决定左右（偶数索引=左，奇数索引=右）
            side = "left" if speakers_seen.index(speaker) % 2 == 0 else "right"
            bubbles.append(
                f'<section data-container="dialogue-bubble" data-side="{side}">'
                f'<p data-container="dialogue-speaker">{speaker}</p>'
                f'<p data-container="dialogue-text">{text}</p>'
                f'</section>'
            )

    return (
        f'<section data-container="dialogue">'
        f'<p data-container="dialogue-title">{title}</p>'
        f'{"".join(bubbles)}'
        f'</section>'
    )


def md_to_html(content: str) -> str:
    """Markdown 转 HTML"""
    html = markdown.markdown(
        content,
        extensions=["tables", "fenced_code", "nl2br"],
    )
    return html


# ── 核心：内联样式注入 ──────────────────────────────────────────────────
def build_style_string(props: dict) -> str:
    """把主题 JSON 的属性字典转成 CSS style 字符串

    JSON key 下划线 → CSS 连字符: font_size → font-size
    margin 分拆写法自动合并为简写——margin-top/bottom 分开写在公众号编辑器
    有被丢弃的报告（doocs/md #504，2025-01），简写更稳
    """
    props = dict(props)
    if "margin" not in props and "margin_top" in props and "margin_bottom" in props:
        top = props.pop("margin_top")
        bottom = props.pop("margin_bottom")
        left = props.pop("margin_left", "0")
        right = props.pop("margin_right", "0")
        if left == right:
            props["margin"] = f"{top} {right} {bottom}"
        else:
            props["margin"] = f"{top} {right} {bottom} {left}"
    parts = []
    for key, val in props.items():
        css_key = key.replace("_", "-")
        parts.append(f"{css_key}:{val}")
    return ";".join(parts)


def _auto_dark_mode(theme: dict) -> dict:
    """自动补全深色模式配置

    对于主题 dark_mode 中未声明的常见标签，根据其亮色样式自动生成深色模式颜色。
    """
    dark_mode = dict(theme.get("dark_mode", {}))
    styles = theme.get("styles", {})

    # 需要自动补全的标签及其深色模式默认色
    auto_tags = {
        "p":              {"color": "#c8c8c8"},
        "strong":         {"color": "#e0a060"},  # 保持强调感
        "em":             {"color": "#a0a0a0"},
        "h3":             {"color": "#d0d0d0"},
        "h4":             {"color": "#c8c8c8"},
        "h5":             {"color": "#b0b0b0"},
        "h6":             {"color": "#999999"},
        "td":             {"color": "#c0c0c0", "bgcolor": "#1e1e1e"},
        "list_item_text": {"color": "#c8c8c8"},
        "footnote_item":  {"color": "#888888"},
        "footnote_title": {"color": "#888888"},
        "callout_content": {"color": "#c0c0c0"},
        "blockquote":      {"color": "#c0c0c0", "bgcolor": "rgba(255,255,255,0.05)"},
    }

    for tag, defaults in auto_tags.items():
        if tag in dark_mode:
            continue  # 主题已显式声明，不覆盖
        if tag not in styles:
            continue  # 主题不使用此标签
        # 检查亮色模式的颜色，只在有明确浅色系颜色时才添加深色覆盖
        tag_styles = styles[tag]
        has_color = any(k in tag_styles for k in ("color", "background", "background_color"))
        if has_color:
            dark_mode[tag] = defaults

    return dark_mode


def inject_dark_mode_attrs(html: str, dark_mode: dict, style_map: dict) -> str:
    """为微信深色模式添加 data-darkmode-* 属性

    通过匹配元素的 style 字符串来定位目标元素，
    然后添加对应的深色模式颜色覆盖。
    """
    for tag_key, dark_cfg in dark_mode.items():
        if tag_key not in style_map:
            continue
        style_str = style_map[tag_key]
        if not style_str:
            continue
        attrs = []
        if "bgcolor" in dark_cfg:
            attrs.append(f'data-darkmode-bgcolor="{dark_cfg["bgcolor"]}"')
        if "color" in dark_cfg:
            attrs.append(f'data-darkmode-color="{dark_cfg["color"]}"')
        if not attrs:
            continue
        dark_attr_str = " ".join(attrs)
        html = html.replace(
            f'style="{style_str}"',
            f'style="{style_str}" {dark_attr_str}',
        )
    return html


def _basic_syntax_highlight(code_html: str) -> str:
    """增强语法高亮：注释、字符串、关键字、数字、装饰器、类型"""
    # 装饰器 @xxx
    code_html = re.sub(
        r'(@\w+)',
        r'<span style="color:#c586c0">\1</span>',
        code_html
    )
    # 单行注释 // ... 和 # ...（排除 URL 中的 ://）
    code_html = re.sub(
        r'(?<!:)(//.*?)(<br>|$)',
        r'<span style="color:#6a9955">\1</span>\2',
        code_html
    )
    code_html = re.sub(
        r'(#[^{].*?)(<br>|$)',
        r'<span style="color:#6a9955">\1</span>\2',
        code_html
    )
    # f-string: f"..." / f'...'（Python）
    code_html = re.sub(
        r'(f&quot;.*?&quot;|f&#x27;.*?&#x27;|f"[^"<]*?"|f\'[^\'<]*?\')',
        r'<span style="color:#ce9178">\1</span>',
        code_html
    )
    # 模板字符串 `...`（JS）
    code_html = re.sub(
        r'(`[^`<]*?`)',
        r'<span style="color:#ce9178">\1</span>',
        code_html
    )
    # 字符串（双引号和单引号，HTML 转义形式）
    code_html = re.sub(
        r'(&quot;.*?&quot;|&#x27;.*?&#x27;|"[^"<]*?"|\'[^\'<]*?\')',
        r'<span style="color:#ce9178">\1</span>',
        code_html
    )
    # 数字（整数和浮点数）
    code_html = re.sub(
        r'(?<![a-zA-Z0-9_])(\d+\.?\d*)',
        r'<span style="color:#b5cea8">\1</span>',
        code_html
    )
    # 常见关键字
    keywords = [
        'function', 'const', 'let', 'var', 'return', 'if', 'else', 'for', 'while',
        'import', 'from', 'export', 'class', 'def', 'print', 'async', 'await',
        'try', 'catch', 'throw', 'new', 'this', 'true', 'false', 'null', 'None',
        'True', 'False', 'elif', 'except', 'finally', 'with', 'as', 'in', 'not',
        'and', 'or', 'is', 'lambda', 'yield', 'pass', 'break', 'continue',
        'do', 'switch', 'case', 'default', 'typeof', 'instanceof', 'void',
        'struct', 'enum', 'impl', 'trait', 'pub', 'fn', 'mut', 'self', 'Self',
        'type', 'interface', 'extends', 'implements', 'abstract', 'static',
        'raise', 'del', 'global', 'nonlocal', 'assert',
    ]
    for kw in keywords:
        code_html = re.sub(
            rf'(?<![a-zA-Z0-9_])({kw})(?![a-zA-Z0-9_])',
            rf'<span style="color:#569cd6">\1</span>',
            code_html
        )
    # 内置类型/函数
    builtins = [
        'int', 'str', 'float', 'bool', 'list', 'dict', 'set', 'tuple',
        'len', 'range', 'enumerate', 'zip', 'map', 'filter', 'sorted',
        'String', 'Number', 'Boolean', 'Array', 'Object', 'Promise',
        'console', 'document', 'window', 'Math', 'JSON', 'Date',
    ]
    for bt in builtins:
        code_html = re.sub(
            rf'(?<![a-zA-Z0-9_])({bt})(?![a-zA-Z0-9_])',
            rf'<span style="color:#4ec9b0">\1</span>',
            code_html
        )
    return code_html


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """将 #RRGGBB 转为 (r, g, b) 元组"""
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _inject_container_styles(html: str, theme: dict) -> str:
    """为围栏容器注入内联样式

    所有样式硬编码，不依赖主题 JSON（除了需要主题 accent 色的地方）。
    """
    # 获取主题 accent 色，用于对话右气泡背景
    accent_hex = theme.get("colors", {}).get("accent", "#07C160")
    r, g, b = _hex_to_rgb(accent_hex)
    right_bubble_bg = f"rgba({r},{g},{b},0.08)"

    # ── dialogue 容器 ──
    dialogue_container = "margin:20px 0;padding:16px;background:#f8f9fa;border-radius:12px"
    dialogue_title = "text-align:center;font-size:14px;color:#999;margin-bottom:12px"
    dialogue_speaker = "font-size:12px;color:#999;margin-bottom:4px"
    dialogue_text = "font-size:15px;color:#333;line-height:1.6;margin:0"
    left_bubble = f"max-width:80%;background:#fff;border-radius:0 12px 12px 12px;padding:10px 14px;margin:8px 20% 8px 0;box-shadow:0 1px 2px rgba(0,0,0,0.05)"
    right_bubble = f"max-width:80%;background:{right_bubble_bg};border-radius:12px 0 12px 12px;padding:10px 14px;margin:8px 0 8px 20%;box-shadow:0 1px 2px rgba(0,0,0,0.05)"

    html = html.replace(
        '<section data-container="dialogue">',
        f'<section data-container="dialogue" style="{dialogue_container}">'
    )
    html = html.replace(
        '<p data-container="dialogue-title">',
        f'<p data-container="dialogue-title" style="{dialogue_title}">'
    )
    html = re.sub(
        r'<section data-container="dialogue-bubble" data-side="left">',
        f'<section data-container="dialogue-bubble" data-side="left" style="{left_bubble}">',
        html,
    )
    html = re.sub(
        r'<section data-container="dialogue-bubble" data-side="right">',
        f'<section data-container="dialogue-bubble" data-side="right" style="{right_bubble}">',
        html,
    )
    html = html.replace(
        '<p data-container="dialogue-speaker">',
        f'<p data-container="dialogue-speaker" style="{dialogue_speaker}">'
    )
    html = html.replace(
        '<p data-container="dialogue-text">',
        f'<p data-container="dialogue-text" style="{dialogue_text}">'
    )

    # ── gallery 容器 ──
    gallery_container = "margin:20px 0"
    gallery_title = "text-align:center;font-size:14px;color:#999;margin-bottom:12px"
    gallery_scroll = "display:flex;overflow-x:auto;gap:8px;padding:4px 0;-webkit-overflow-scrolling:touch;touch-action:pan-x"
    gallery_img = "height:200px;width:auto;border-radius:8px;flex-shrink:0"

    html = html.replace(
        '<section data-container="gallery">',
        f'<section data-container="gallery" style="{gallery_container}">'
    )
    html = html.replace(
        '<p data-container="gallery-title">',
        f'<p data-container="gallery-title" style="{gallery_title}">'
    )
    html = html.replace(
        '<section data-container="gallery-scroll">',
        f'<section data-container="gallery-scroll" style="{gallery_scroll}">'
    )
    # gallery 内部图片需要特殊样式（覆盖默认 img 样式）
    html = re.sub(
        r'(<section data-container="gallery-scroll"[^>]*>)(.*?)(</section>)',
        lambda m: m.group(1) + re.sub(
            r'<img ',
            f'<img style="{gallery_img}" ',
            m.group(2)
        ) + m.group(3),
        html,
        flags=re.DOTALL,
    )

    # ── longimage 容器 ──
    longimage_container = "margin:20px 0"
    longimage_title = "text-align:center;font-size:14px;color:#999;margin-bottom:12px"
    longimage_scroll = "max-height:500px;overflow-y:auto;-webkit-overflow-scrolling:touch;border-radius:8px;border:1px solid #eee"
    longimage_img = "width:100%;display:block"

    html = html.replace(
        '<section data-container="longimage">',
        f'<section data-container="longimage" style="{longimage_container}">'
    )
    html = html.replace(
        '<p data-container="longimage-title">',
        f'<p data-container="longimage-title" style="{longimage_title}">'
    )
    html = html.replace(
        '<section data-container="longimage-scroll">',
        f'<section data-container="longimage-scroll" style="{longimage_scroll}">'
    )
    # longimage 内部图片样式
    html = re.sub(
        r'(<section data-container="longimage-scroll"[^>]*>)(.*?)(</section>)',
        lambda m: m.group(1) + re.sub(
            r'<img ',
            f'<img style="{longimage_img}" ',
            m.group(2)
        ) + m.group(3),
        html,
        flags=re.DOTALL,
    )

    # ── stat 容器（关键数字）──
    accent_04 = f"rgba({r},{g},{b},0.04)"
    stat_container = f"text-align:center;padding:24px 16px;margin:20px 0;background:{accent_04};border-radius:12px"
    stat_label = "font-size:14px;color:#666;margin:0"

    html = html.replace(
        '<section data-container="stat">',
        f'<section data-container="stat" style="{stat_container}">'
    )

    # 数字按字符数自适应字号：长数字（如 1,000,000 / 带百分号）不撑爆手机屏
    def _stat_number_style(value: str) -> str:
        n = len(value.strip())
        size = 48 if n <= 4 else (40 if n <= 7 else 32)
        return f"font-size:{size}px;font-weight:800;color:{accent_hex};line-height:1.2;margin:0 0 4px 0"

    html = re.sub(
        r'<p data-container="stat-number">([^<]*)</p>',
        lambda m: f'<p data-container="stat-number" style="{_stat_number_style(m.group(1))}">{m.group(1)}</p>',
        html,
    )
    html = html.replace(
        '<p data-container="stat-label">',
        f'<p data-container="stat-label" style="{stat_label}">'
    )

    # ── stat-row 多数据横排 ──
    stat_row = "display:flex;gap:8px;margin:20px 0"
    stat_item = f"flex:1;text-align:center;padding:20px 12px;background:{accent_04};border-radius:10px"
    html = html.replace(
        '<section data-container="stat-row">',
        f'<section data-container="stat-row" style="{stat_row}">'
    )
    html = html.replace(
        '<section data-container="stat-item">',
        f'<section data-container="stat-item" style="{stat_item}">'
    )

    # ── byline 容器（作者总结）──
    byline_container = f"margin:24px 0;padding:20px 24px;background:{accent_04};border-radius:12px;border-left:3px solid {accent_hex}"
    byline_author = f"font-size:16px;font-weight:700;color:{accent_hex};margin:0 0 8px"
    byline_content = "font-size:15px;color:#555;line-height:1.8;margin:0"
    html = html.replace(
        '<section data-container="byline">',
        f'<section data-container="byline" style="{byline_container}">'
    )
    html = html.replace(
        '<p data-container="byline-author">',
        f'<p data-container="byline-author" style="{byline_author}">'
    )
    html = html.replace(
        '<p data-container="byline-content">',
        f'<p data-container="byline-content" style="{byline_content}">'
    )

    # ── video 容器（视频卡片）──
    video_container = "display:flex;align-items:center;margin:24px 0;padding:14px 16px;background:#1F2329;border-radius:12px"
    video_badge = (
        f"display:inline-flex;align-items:center;justify-content:center;"
        f"min-width:44px;width:44px;height:44px;border-radius:50%;"
        f"background:{accent_hex};color:#fff;font-size:16px;margin-right:14px;flex-shrink:0"
    )
    video_info = "flex:1;min-width:0"
    video_title = "font-size:15px;color:#fff;font-weight:bold;margin:0 0 4px;line-height:1.5"
    video_desc = "font-size:13px;color:#9AA3AF;margin:0 0 4px;line-height:1.5"
    video_link = "font-size:13px;color:#9AA3AF;margin:0;word-break:break-all"
    html = html.replace('<section data-container="video">', f'<section data-container="video" style="{video_container}">')
    html = html.replace('<section data-container="video-badge">', f'<section data-container="video-badge" style="{video_badge}">')
    html = html.replace('<section data-container="video-info">', f'<section data-container="video-info" style="{video_info}">')
    html = html.replace('<p data-container="video-title">', f'<p data-container="video-title" style="{video_title}">')
    html = html.replace('<p data-container="video-desc">', f'<p data-container="video-desc" style="{video_desc}">')
    html = html.replace('<p data-container="video-link">', f'<p data-container="video-link" style="{video_link}">')

    # ── intro 容器（文首导读块）──
    intro_container = f"margin:20px 0 28px;padding:14px 16px;background:{accent_04};border-left:4px solid {accent_hex};border-radius:0 8px 8px 0"
    intro_label = f"font-size:13px;font-weight:bold;color:{accent_hex};margin:0 0 6px;letter-spacing:2px"
    intro_content = "font-size:15px;color:#555;line-height:1.8;margin:0"
    html = html.replace('<section data-container="intro">', f'<section data-container="intro" style="{intro_container}">')
    html = html.replace('<p data-container="intro-label">', f'<p data-container="intro-label" style="{intro_label}">')
    html = html.replace('<p data-container="intro-content">', f'<p data-container="intro-content" style="{intro_content}">')

    # ── end 容器（END 结束符）──
    end_container = "margin:36px 0 20px"
    end_row = "display:flex;align-items:center;justify-content:center"
    end_line = f"display:inline-block;width:56px;height:1px;background:rgba({r},{g},{b},0.4)"
    end_text = f"font-size:13px;font-weight:bold;color:{accent_hex};letter-spacing:4px;margin:0 14px"
    end_cta = "font-size:13px;color:#999;margin:12px 0 0;text-align:center"
    html = html.replace('<section data-container="end">', f'<section data-container="end" style="{end_container}">')
    html = html.replace('<section data-container="end-row">', f'<section data-container="end-row" style="{end_row}">')
    html = html.replace('<section data-container="end-line">', f'<section data-container="end-line" style="{end_line}">')
    html = html.replace('<p data-container="end-text">', f'<p data-container="end-text" style="{end_text}">')
    html = html.replace('<p data-container="end-cta">', f'<p data-container="end-cta" style="{end_cta}">')

    # ── history 容器（往期回顾卡）──
    history_container = "margin:28px 0;padding:16px;background:#FAFAFA;border:1px solid #EEEEEE;border-radius:10px"
    history_title = f"font-size:14px;font-weight:bold;color:{accent_hex};margin:0 0 10px;letter-spacing:1px"
    history_item = "font-size:14px;color:#555;margin:0 0 8px;line-height:1.6"
    html = html.replace('<section data-container="history">', f'<section data-container="history" style="{history_container}">')
    html = html.replace('<p data-container="history-title">', f'<p data-container="history-title" style="{history_title}">')
    html = html.replace('<p data-container="history-item">', f'<p data-container="history-item" style="{history_item}">')

    # ── timeline 容器（时间线）──
    accent_20 = f"rgba({r},{g},{b},0.2)"
    timeline_container = "margin:20px 0;padding:16px"
    timeline_title = "text-align:center;font-size:14px;color:#999;margin-bottom:16px"
    timeline_item = "display:flex;margin-bottom:12px"
    timeline_time = f"min-width:80px;font-size:14px;font-weight:700;color:{accent_hex};text-align:right;padding-right:16px"
    timeline_dot = f"color:{accent_hex};font-size:12px;flex-shrink:0;margin-top:2px;line-height:1"
    timeline_content = f"font-size:15px;color:#333;line-height:1.6;padding-bottom:16px;border-left:2px solid {accent_20};padding-left:12px;margin-left:5px"

    html = html.replace(
        '<section data-container="timeline">',
        f'<section data-container="timeline" style="{timeline_container}">'
    )
    html = html.replace(
        '<p data-container="timeline-title">',
        f'<p data-container="timeline-title" style="{timeline_title}">'
    )
    html = html.replace(
        '<section data-container="timeline-item">',
        f'<section data-container="timeline-item" style="{timeline_item}">'
    )
    html = html.replace(
        '<span data-container="timeline-time">',
        f'<span data-container="timeline-time" style="{timeline_time}">'
    )
    html = html.replace(
        '<span data-container="timeline-dot">',
        f'<span data-container="timeline-dot" style="{timeline_dot}">'
    )
    html = html.replace(
        '<span data-container="timeline-content">',
        f'<span data-container="timeline-content" style="{timeline_content}">'
    )

    # ── steps 容器（步骤流程）──
    steps_container = "margin:20px 0;padding:16px"
    steps_title = "text-align:center;font-size:14px;color:#999;margin-bottom:16px"
    steps_item = "display:flex;align-items:flex-start;margin-bottom:12px"
    steps_number = f"display:inline-flex;width:28px;height:28px;border-radius:50%;background:{accent_hex};color:#fff;font-size:14px;font-weight:700;align-items:center;justify-content:center;flex-shrink:0;margin-right:12px;line-height:1"
    steps_content = "font-size:15px;color:#333;line-height:1.6;padding-top:3px"

    html = html.replace(
        '<section data-container="steps">',
        f'<section data-container="steps" style="{steps_container}">'
    )
    html = html.replace(
        '<p data-container="steps-title">',
        f'<p data-container="steps-title" style="{steps_title}">'
    )
    html = html.replace(
        '<section data-container="steps-item">',
        f'<section data-container="steps-item" style="{steps_item}">'
    )
    html = html.replace(
        '<span data-container="steps-number">',
        f'<span data-container="steps-number" style="{steps_number}">'
    )
    html = html.replace(
        '<span data-container="steps-content">',
        f'<span data-container="steps-content" style="{steps_content}">'
    )

    # ── compare 容器（对比卡片）──
    compare_container = "margin:20px 0;padding:16px"
    compare_header = "display:flex;margin-bottom:8px"
    compare_header_cell = f"flex:1;text-align:center;font-weight:700;color:{accent_hex};padding:8px"
    compare_row = "display:flex;border-top:1px solid #eee;padding:8px 0"
    compare_left = "flex:1;text-align:center;font-size:14px;color:#666;padding:8px"
    compare_right = "flex:1;text-align:center;font-size:14px;color:#333;padding:8px;font-weight:600"

    html = html.replace(
        '<section data-container="compare">',
        f'<section data-container="compare" style="{compare_container}">'
    )
    html = html.replace(
        '<section data-container="compare-header">',
        f'<section data-container="compare-header" style="{compare_header}">'
    )
    html = html.replace(
        '<span data-container="compare-header-left">',
        f'<span data-container="compare-header-left" style="{compare_header_cell}">'
    )
    html = html.replace(
        '<span data-container="compare-header-right">',
        f'<span data-container="compare-header-right" style="{compare_header_cell}">'
    )
    html = html.replace(
        '<section data-container="compare-row">',
        f'<section data-container="compare-row" style="{compare_row}">'
    )
    html = html.replace(
        '<span data-container="compare-left">',
        f'<span data-container="compare-left" style="{compare_left}">'
    )
    html = html.replace(
        '<span data-container="compare-right">',
        f'<span data-container="compare-right" style="{compare_right}">'
    )

    # ── quote-card 容器（人物引言）──
    accent_03 = f"rgba({r},{g},{b},0.03)"
    accent_15 = f"rgba({r},{g},{b},0.15)"
    quote_container = f"margin:24px 0;padding:20px 24px;background:{accent_03};border-radius:12px;border-left:3px solid {accent_hex}"
    quote_mark = f"font-size:36px;color:{accent_15};margin:0;line-height:1"
    quote_text = "font-size:17px;color:#333;line-height:1.8;margin:8px 0 12px;font-style:italic"
    quote_author = "font-size:13px;color:#999;text-align:right;margin:0"

    html = html.replace(
        '<section data-container="quote-card">',
        f'<section data-container="quote-card" style="{quote_container}">'
    )
    html = html.replace(
        '<p data-container="quote-mark">',
        f'<p data-container="quote-mark" style="{quote_mark}">'
    )
    html = html.replace(
        '<p data-container="quote-text">',
        f'<p data-container="quote-text" style="{quote_text}">'
    )
    html = html.replace(
        '<p data-container="quote-author">',
        f'<p data-container="quote-author" style="{quote_author}">'
    )

    return html


def _hero_bold_paragraphs_to_cards(html: str, accent: str) -> str:
    """检测 3+ 连续「加粗标题：描述」段落，转成卡片组。

    匹配模式: <p ...><strong ...>标题（副标题）：</strong> 描述</p> 连续 3 个以上
    """
    # 匹配单个加粗开头段落：<p ...><strong ...>XXX</strong> YYY</p>
    bold_p_re = re.compile(
        r'<p\s[^>]*><strong\s[^>]*>(.*?)</strong>\s*(.*?)</p>',
        re.DOTALL
    )

    # 找所有连续的加粗段落块
    all_matches = list(bold_p_re.finditer(html))
    if len(all_matches) < 3:
        return html

    # 找连续组（相邻 match，中间只有空白）
    groups = []
    current_group = [all_matches[0]]
    for i in range(1, len(all_matches)):
        between = html[all_matches[i-1].end():all_matches[i].start()].strip()
        if not between:  # 紧邻
            current_group.append(all_matches[i])
        else:
            if len(current_group) >= 3:
                groups.append(current_group)
            current_group = [all_matches[i]]
    if len(current_group) >= 3:
        groups.append(current_group)

    if not groups:
        return html

    # 从后往前替换，防止偏移
    for group in reversed(groups):
        cards_html = f'<div style="display:flex;flex-direction:column;gap:10px;margin:16px 0;">'
        for idx, m in enumerate(group, 1):
            bold_text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            desc_text = m.group(2).strip()
            # 去掉末尾的标点
            label = bold_text.rstrip('：:')
            # 尝试提取主标题和副标题（括号里的）
            label_match = re.match(r'^(.+?)[（(](.+?)[）)]$', label)
            if label_match:
                main_label = label_match.group(1).strip()
                sub_label = label_match.group(2).strip()
            else:
                main_label = label
                sub_label = ""

            badge = f'Layer {idx}'
            desc_clean = re.sub(r'<[^>]+>', '', desc_text).strip()

            cards_html += (
                f'<div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;display:flex;align-items:center;">'
                f'<span style="background:{accent};color:#fff;font-size:11px;font-weight:700;padding:3px 8px;border-radius:4px;margin-right:12px;white-space:nowrap;">{badge}</span>'
                f'<div>'
                f'<p style="font-size:14px;font-weight:600;color:#1a1a1a;margin:0;">{main_label}</p>'
            )
            if sub_label or desc_clean:
                summary = sub_label or desc_clean[:40]
                cards_html += f'<p style="font-size:12px;color:#888;margin:2px 0 0;">{summary}</p>'
            cards_html += '</div></div>'
        cards_html += '</div>'

        start = group[0].start()
        end = group[-1].end()
        html = html[:start] + cards_html + html[end:]

    return html


def _hex_to_rgba(hex_str: str, alpha: float) -> str:
    """#RRGGBB → rgba(r,g,b,a)。解析失败返回 accent fallback。"""
    h = hex_str.lstrip('#')
    if len(h) != 6:
        return f"rgba(99,102,241,{alpha})"
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return f"rgba(99,102,241,{alpha})"
    return f"rgba({r},{g},{b},{alpha})"


def _hero_table_to_flex(html: str, hero_cfg: dict) -> str:
    """将双列 table 转为 flex 手机友好布局（hero 布局专用）。
    根据主题 section_bg 亮暗自适应单元格颜色、边框、斑马色。"""
    accent = hero_cfg.get("accent", "#6366f1")
    section_bg = hero_cfg.get("section_bg", "#ffffff")
    text_color = hero_cfg.get("text_color", "#555")

    # 判断主题是否暗色
    def _is_dark(hex_color: str) -> bool:
        h = hex_color.lstrip('#')
        if len(h) != 6:
            return False
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            return False
        return (0.299 * r + 0.587 * g + 0.114 * b) < 128

    if _is_dark(section_bg):
        cell_text = text_color
        border_color = "rgba(255,255,255,0.10)"
        alt_row_bg = "rgba(255,255,255,0.04)"
        header_bg = _hex_to_rgba(accent, 0.12)
    else:
        cell_text = "#555"
        border_color = "#e5e7eb"
        alt_row_bg = "#fafafa"
        header_bg = _hex_to_rgba(accent, 0.06)

    table_re = re.compile(r'<table\s[^>]*>.*?</table>', re.DOTALL)

    def convert_table(match):
        table_html = match.group(0)
        # 提取表头
        th_matches = re.findall(r'<th\s[^>]*>(.*?)</th>', table_html, re.DOTALL)
        if len(th_matches) != 2:
            return table_html  # 只处理双列

        # 提取行数据（tr 可能有 style 属性，如 zebra stripes）
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
        data_rows = []
        for row in rows:
            tds = re.findall(r'<td\s[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(tds) == 2:
                data_rows.append(tds)

        if not data_rows:
            return table_html

        header_left = re.sub(r'<[^>]+>', '', th_matches[0]).strip()
        header_right = re.sub(r'<[^>]+>', '', th_matches[1]).strip()

        # 构建 flex 表格
        flex = f'<div style="border-radius:10px;overflow:hidden;border:1px solid {border_color};margin:20px 0;">'
        # 表头
        flex += (
            f'<div style="display:flex;background:{header_bg};">'
            f'<div style="flex:1;padding:10px 14px;font-size:13px;font-weight:600;color:{accent};border-right:1px solid {border_color};">{header_left}</div>'
            f'<div style="flex:1;padding:10px 14px;font-size:13px;font-weight:600;color:{accent};">{header_right}</div>'
            f'</div>'
        )
        # 数据行
        for i, (left, right) in enumerate(data_rows):
            left_text = re.sub(r'<[^>]+>', '', left).strip()
            right_text = re.sub(r'<[^>]+>', '', right).strip()
            row_bg = f'background:{alt_row_bg};' if i % 2 == 1 else ''
            flex += (
                f'<div style="display:flex;border-top:1px solid {border_color};{row_bg}">'
                f'<div style="flex:1;padding:10px 14px;font-size:13px;color:{cell_text};border-right:1px solid {border_color};">{left_text}</div>'
                f'<div style="flex:1;padding:10px 14px;font-size:13px;color:{cell_text};">{right_text}</div>'
                f'</div>'
            )
        flex += '</div>'
        return flex

    return table_re.sub(convert_table, html)


def _wrap_timeline_sections(html: str, tl_cfg: dict) -> str:
    """时间线布局：左侧竖线+圆点节点，每个 h2 是一个节点。"""
    accent = tl_cfg.get("accent", "#16a34a")
    accent_light = tl_cfg.get("accent_light", "#bbf7d0")
    accent_bg = tl_cfg.get("accent_bg", "#f0fdf4")
    heading_color = tl_cfg.get("heading_color", "#1a1a1a")
    line_color = tl_cfg.get("line_color", accent)
    dot_size = 14

    parts = re.split(r'(?=<h[12]\s)', html)
    if not parts:
        return html

    sections = []
    is_first = True
    nodes = []

    for part in parts:
        part = part.strip()
        if not part:
            continue

        h1_match = re.match(r'<h1\s[^>]*>(.*?)</h1>', part, re.DOTALL)
        h2_match = re.match(r'<h2\s[^>]*>(.*?)</h2>', part, re.DOTALL)

        if h1_match and is_first:
            title_text = re.sub(r'<[^>]+>', '', h1_match.group(1)).strip()
            rest = part[h1_match.end():].strip()
            # 白底标题
            author_line = (
                f'<p style="color:#999;font-size:13px;text-align:center;margin:0 0 24px;">{HEADER_AUTHOR}</p>'
                if HEADER_AUTHOR else ''
            )
            header = (
                f'<section style="padding:32px 24px;background:#ffffff;">'
                f'<h1 style="font-size:22px;font-weight:700;color:{heading_color};text-align:center;margin:0 0 8px;line-height:1.3;">{title_text}</h1>'
                f'{author_line}'
            )
            if rest:
                header += rest
            header += '</section>'
            sections.append(header)
            is_first = False

        elif h2_match:
            h2_text = re.sub(r'<[^>]+>', '', h2_match.group(1)).strip()
            rest = part[h2_match.end():].strip()
            rest = re.sub(r'<hr\s[^>]*/?\s*>', '', rest)  # 时间线布局不需要 hr
            nodes.append((h2_text, rest))
        else:
            if part:
                if nodes:
                    # 追加到最后一个节点
                    last_title, last_content = nodes[-1]
                    nodes[-1] = (last_title, last_content + part)
                else:
                    sections.append(f'<section style="padding:24px 28px;background:#ffffff;">{part}</section>')

    # 构建时间线容器
    if nodes:
        tl_html = f'<section style="padding:24px 24px 12px;background:#ffffff;">'
        tl_html += f'<div style="border-left:3px solid {line_color};padding-left:24px;margin-left:8px;">'

        for i, (title, content) in enumerate(nodes):
            is_last = (i == len(nodes) - 1)
            # 圆点（用 inline-block + margin 负值代替 position:absolute，微信兼容）
            dot_style = (
                f'display:inline-block;width:{dot_size}px;height:{dot_size}px;'
                f'margin-left:-33px;margin-right:12px;vertical-align:middle;'
                f'background:{accent};border-radius:50%;border:3px solid #ffffff;box-shadow:0 0 0 2px {accent};'
            )
            if is_last:
                # 终点：空心圆
                dot_style = (
                    f'display:inline-block;width:18px;height:18px;'
                    f'margin-left:-35px;margin-right:10px;vertical-align:middle;'
                    f'background:#ffffff;border:3px solid {accent};border-radius:50%;'
                )

            node_html = (
                f'<section style="margin-bottom:{36 if not is_last else 0}px;">'
                f'<div style="{dot_style}"></div>'
                f'<h2 style="font-size:17px;font-weight:700;color:{accent};margin:0 0 12px;line-height:1.3;">{title}</h2>'
                f'{content}'
                f'</section>'
            )
            tl_html += node_html

        tl_html += '</div></section>'
        sections.append(tl_html)

    # 简洁结尾
    sections.append(
        f'<section style="padding:20px 28px;background:#ffffff;">'
        f'<p style="font-size:13px;color:#999;text-align:center;margin:0;">— END —</p>'
        f'</section>'
    )

    return ''.join(sections)


def _enhance_hero_byline(html: str, hero_cfg: dict) -> str:
    """hero 布局专属：把 byline 容器升级为居中签名尾部栏。
    上方加点状装饰线、上下加 accent_border 细线、accent_bg 浅底，
    author 居中加粗 accent 色，content 左对齐保留可读性。"""
    accent = hero_cfg.get("accent", "#6366f1")
    accent_bg = hero_cfg.get("accent_bg", "#f8f7ff")
    accent_border = hero_cfg.get("accent_border", "#e0e7ff")

    def replace_byline(m):
        inner = m.group(1)
        author_m = re.search(
            r'<p[^>]*data-container="byline-author"[^>]*>(.*?)</p>', inner, re.DOTALL)
        content_m = re.search(
            r'<p[^>]*data-container="byline-content"[^>]*>(.*?)</p>', inner, re.DOTALL)
        if not author_m or not content_m:
            return m.group(0)
        author = author_m.group(1)
        content = content_m.group(1)
        outer = (
            f'margin:36px 0 0;padding:44px 28px 40px;'
            f'background:{accent_bg};'
            f'border-top:1px solid {accent_border};'
            f'border-bottom:1px solid {accent_border};'
            f'text-align:center;'
        )
        dots = (
            f'<p style="font-size:13px;color:{accent};letter-spacing:6px;'
            f'margin:0 0 18px;">· · ·</p>'
        )
        author_html = (
            f'<p data-container="byline-author" style="font-size:18px;'
            f'font-weight:700;color:{accent};margin:0 0 14px;'
            f'letter-spacing:0.5px;">{author}</p>'
        )
        content_html = (
            f'<p data-container="byline-content" style="font-size:15px;'
            f'color:#555;line-height:1.9;margin:0 auto;max-width:560px;'
            f'text-align:left;">{content}</p>'
        )
        return (
            f'<section data-container="byline" data-hero="1" '
            f'style="{outer}">{dots}{author_html}{content_html}</section>'
        )

    return re.sub(
        r'<section data-container="byline"[^>]*>(.*?)</section>',
        replace_byline, html, flags=re.DOTALL,
    )


def _wrap_hero_sections(html: str, hero_cfg: dict) -> str:
    """通用分节布局引擎。通过 hero_cfg 开关组合出不同风格：

    特性开关（均可在主题 JSON 的 hero 字段中设置）：
      dark_header  (true)  暗色首屏+副标题+引文钩子
      dark_footer  (true)  暗色 END 结尾
      numbered     (true)  h2 前大序号 01/02
      pull_quotes  (true)  短 blockquote → 居中大字穿插
      alt_bg       (true)  奇偶章节交替背景
      h2_border    (null)  h2 底部装饰线，如 "2px solid #e8590c"
      cards        (false) 连续加粗段落 → Layer 卡片组（公众号不兼容，默认关）
      flex_table   (false) 双列表格 → flex 手机布局（公众号剥离 flex，默认关，用原生 table）
    """
    accent = hero_cfg.get("accent", "#6366f1")
    accent_light = hero_cfg.get("accent_light", "#a5b4fc")
    accent_bg = hero_cfg.get("accent_bg", "#f8f7ff")
    accent_border = hero_cfg.get("accent_border", "#e0e7ff")
    dark_bg = hero_cfg.get("dark_bg", "#1a1a2e")
    alt_bg = hero_cfg.get("alt_bg", "#fafafa")
    text_color = hero_cfg.get("text_color", "#333333")
    heading_color = hero_cfg.get("heading_color", "#1a1a1a")
    number_color = hero_cfg.get("number_color", "rgba(99,102,241,0.12)")

    # 特性开关
    feat_dark_header = hero_cfg.get("dark_header", True)
    feat_dark_footer = hero_cfg.get("dark_footer", True)
    feat_numbered = hero_cfg.get("numbered", True)
    feat_pull_quotes = hero_cfg.get("pull_quotes", True)
    feat_alt_bg = hero_cfg.get("alt_bg_enabled", True)
    feat_h2_border = hero_cfg.get("h2_border")  # e.g. "2px solid #e8590c"
    feat_cards = hero_cfg.get("cards", False)
    feat_flex_table = hero_cfg.get("flex_table", False)
    section_bg = hero_cfg.get("section_bg", "#ffffff")  # 暗色主题用深色底

    # 升级 byline 容器为 hero 尾部签名栏（若存在）
    html = _enhance_hero_byline(html, hero_cfg)

    parts = re.split(r'(?=<h[12]\s)', html)
    if not parts:
        return html

    sections = []
    chapter_num = 0
    is_first = True
    first_hook_used = False  # 第一个短 blockquote 做引文钩子

    for part in parts:
        part = part.strip()
        if not part:
            continue

        h1_match = re.match(r'<h1\s[^>]*>(.*?)</h1>', part, re.DOTALL)
        h2_match = re.match(r'<h2\s[^>]*>(.*?)</h2>', part, re.DOTALL)

        if h1_match and is_first:
            title_text = re.sub(r'<[^>]+>', '', h1_match.group(1)).strip()
            rest = part[h1_match.end():].strip()

            if feat_dark_header:
                # 暗色首屏：提取第一段做副标题（跳过仅含图片的段）
                paragraphs = re.findall(r'<p\s[^>]*>(.*?)</p>', rest, re.DOTALL)
                subtitle = ""
                if paragraphs:
                    # 跳过首段若只包含 <img>（封面图不应当副标题）
                    first_text_para = None
                    for p in paragraphs:
                        # 剥离标签看是否有文本
                        text_only = re.sub(r'<[^>]+>', '', p).strip()
                        if text_only:
                            first_text_para = p
                            break
                    if first_text_para:
                        sub_html = first_text_para
                        sub_html = re.sub(
                            r'<strong[^>]*>(.*?)</strong>',
                            f'<span style="color:{accent_light};font-weight:600;">\\1</span>',
                            sub_html
                        )
                        subtitle = sub_html

                kicker_line = (
                    f'<p style="font-size:12px;color:rgba(255,255,255,0.5);margin:0 0 16px;letter-spacing:2px;">{HEADER_AUTHOR} · 深度解读</p>'
                    if HEADER_AUTHOR else ''
                )
                hero = (
                    f'<section style="padding:40px 28px 32px;background:{dark_bg};color:#ffffff;">'
                    f'{kicker_line}'
                    f'<h1 style="font-size:24px;font-weight:800;color:#ffffff;margin:0 0 20px;line-height:1.35;letter-spacing:-0.3px;">{title_text}</h1>'
                    f'<div style="width:40px;height:3px;background:{accent};border-radius:2px;margin:0 0 20px;"></div>'
                )
                if subtitle:
                    hero += f'<p style="font-size:15px;color:rgba(255,255,255,0.75);line-height:1.7;margin:0;">{subtitle}</p>'
                hero += '</section>'
                sections.append(hero)

                # 引文钩子（暗色首屏下方）
                if feat_pull_quotes:
                    bq_hook = re.search(r'<blockquote[^>]*>(.*?)</blockquote>', rest, re.DOTALL)
                    if bq_hook:
                        hook_inner = re.sub(r'</?p[^>]*>', '', bq_hook.group(1)).strip()
                        hook_text = re.sub(r'<[^>]+>', '', hook_inner).strip()
                        if len(hook_text) < 120:
                            hook_section = (
                                f'<section style="padding:28px;background:{accent_bg};border-bottom:1px solid {accent_border};">'
                                f'<p style="font-size:22px;font-weight:300;color:{accent};line-height:1.5;margin:0;text-align:center;letter-spacing:-0.2px;">{hook_text}</p>'
                                f'</section>'
                            )
                            sections.append(hook_section)
                            rest = rest[:bq_hook.start()] + rest[bq_hook.end():]
                            first_hook_used = True

                # 剩余首段内容（底部 padding 收窄，避免与下一章节 padding 叠加成大间距）
                if paragraphs and len(paragraphs) > 1:
                    remaining = rest
                    first_p_match = re.search(r'<p\s[^>]*>.*?</p>', remaining, re.DOTALL)
                    if first_p_match:
                        remaining = remaining[first_p_match.end():].strip()
                    if remaining:
                        sections.append(f'<section style="padding:20px 28px 0;background:{section_bg};">{remaining}</section>')
                elif rest:
                    first_p_match = re.search(r'<p\s[^>]*>.*?</p>', rest, re.DOTALL)
                    if first_p_match:
                        remaining = rest[first_p_match.end():].strip()
                        if remaining:
                            sections.append(f'<section style="padding:20px 28px 0;background:{section_bg};">{remaining}</section>')
            else:
                # 标题区 + 正文合并为一个 section，避免 padding 叠加
                author_line = (
                    f'<p style="color:#999;font-size:13px;text-align:center;margin:0 0 24px;">{HEADER_AUTHOR}</p>'
                    if HEADER_AUTHOR else ''
                )
                intro_html = (
                    f'<section style="padding:28px 28px 0;background:{section_bg};">'
                    f'<h1 style="font-size:22px;font-weight:700;color:{heading_color};text-align:center;margin:0 0 8px;line-height:1.3;">{title_text}</h1>'
                    f'{author_line}'
                )
                if rest:
                    intro_html += rest
                intro_html += '</section>'
                sections.append(intro_html)

            is_first = False

        elif h2_match:
            chapter_num += 1
            h2_text = re.sub(r'<[^>]+>', '', h2_match.group(1)).strip()
            rest = part[h2_match.end():].strip()

            bg = alt_bg if (feat_alt_bg and chapter_num % 2 == 0) else section_bg

            # 短 blockquote → 引文穿插（全宽居中大字）
            if feat_pull_quotes:
                def make_pull_quote(bq_match):
                    bq_content = bq_match.group(0)
                    inner = re.sub(r'</?blockquote[^>]*>', '', bq_content)
                    ps = re.findall(r'<p[^>]*>(.*?)</p>', inner, re.DOTALL)
                    if not ps:
                        return bq_content
                    main_text = re.sub(r'<[^>]+>', '', ps[0]).strip()
                    attr_text = ""
                    if len(ps) > 1:
                        # 第二段可能是出处（— Karpathy）
                        attr_text = re.sub(r'<[^>]+>', '', ps[1]).strip()
                    # 也检测 main_text 末尾的署名行（同一段内换行的情况）
                    if not attr_text:
                        attr_match = re.search(r'[—–]\s*(.+)$', main_text)
                        if attr_match:
                            attr_text = attr_match.group(0).strip()
                            main_text = main_text[:attr_match.start()].strip()

                    full_bq = bq_match.group(0)
                    has_link = '<a ' in full_bq or '<a style' in full_bq or 'http' in main_text or 'href=' in full_bq
                    has_footnote = '<sup' in full_bq
                    has_source_keyword = any(kw in main_text for kw in ['来源', '参考', '本文参考', 'Source', 'Reference', '|'])
                    if len(main_text) < 120 and not has_link and not has_footnote and not has_source_keyword:
                        # 区分"出处署名"（— Karpathy）vs"中文翻译/补充"（长文本、含中文）
                        is_attribution = bool(
                            re.match(r'^\s*[—–-]', attr_text)
                            or (attr_text and len(attr_text) <= 20 and not re.search(r'[\u4e00-\u9fff]{5,}', attr_text))
                        )
                        attr_html = ""
                        if attr_text:
                            if is_attribution:
                                attr_html = f'<p style="font-size:12px;color:#aaa;text-align:center;margin:10px 0 0;">{attr_text}</p>'
                            else:
                                # 翻译/补充：字号适中、颜色不灰、与英文拉近呼吸感
                                attr_html = f'<p style="font-size:15px;color:#666;line-height:1.7;text-align:center;margin:14px 0 0;">{attr_text}</p>'
                        return (
                            f'</section>'
                            f'<section style="padding:36px 28px;background:{accent_bg};border-top:1px solid {accent_border};border-bottom:1px solid {accent_border};margin-bottom:24px;">'
                            f'<p style="font-size:20px;font-weight:300;color:{accent};line-height:1.55;margin:0;text-align:center;letter-spacing:-0.2px;font-style:italic;">{main_text}</p>'
                            f'{attr_html}'
                            f'</section>'
                            f'<section style="padding:0 28px 0;background:{bg};">'
                        )
                    return bq_content

                rest = re.sub(r'<blockquote[^>]*>.*?</blockquote>', make_pull_quote, rest, flags=re.DOTALL)

            # hero 布局中 <hr> 多余（section 边界已做分隔），去掉
            rest = re.sub(r'<hr\s[^>]*/?\s*>', '', rest)

            # 连续加粗段落 → 卡片组
            if feat_cards:
                rest = _hero_bold_paragraphs_to_cards(rest, accent)

            # 双列表格 → flex 手机友好布局
            if feat_flex_table:
                rest = _hero_table_to_flex(rest, hero_cfg)

            # 构建章节标题
            if feat_numbered:
                # 大序号 + 标题
                heading_html = (
                    f'<div style="display:flex;align-items:baseline;margin-bottom:28px;">'
                    f'<span style="font-size:42px;font-weight:900;color:{number_color};line-height:1;margin-right:14px;font-family:Georgia,serif;">{chapter_num:02d}</span>'
                    f'<h2 style="font-size:{hero_cfg.get("h2_font_size", "20px")};font-weight:700;color:{heading_color};margin:0;line-height:1.3;">{h2_text}</h2>'
                    f'</div>'
                )
            elif feat_h2_border:
                # 带底部装饰线的标题
                heading_html = (
                    f'<h2 style="font-size:20px;font-weight:700;color:{heading_color};margin:0 0 16px;'
                    f'padding-bottom:12px;border-bottom:{feat_h2_border};">{h2_text}</h2>'
                )
            else:
                # 纯标题
                heading_html = f'<h2 style="font-size:20px;font-weight:700;color:{heading_color};margin:0 0 16px;">{h2_text}</h2>'

            section_html = (
                f'<section style="padding:24px 28px;background:{bg};">'
                f'{heading_html}'
                f'{rest}'
                f'</section>'
            )
            sections.append(section_html)
        else:
            if part:
                sections.append(f'<section style="padding:24px 28px;background:{section_bg};">{part}</section>')

    # 结尾
    if feat_dark_footer:
        sections.append('<!-- HERO_FOOTER -->')
    else:
        # 简洁分隔结尾
        sections.append(
            f'<section style="padding:20px 28px;background:{section_bg};">'
            f'<hr style="border:none;height:1px;background:#e0e0e0;margin:0 0 16px;">'
            f'<p style="font-size:13px;color:#999;text-align:center;margin:0;">— END —</p>'
            f'</section>'
        )

    return ''.join(sections)


def _build_hero_footer(hero_cfg: dict) -> str:
    """构建 hero 布局的暗色结尾 section。"""
    dark_bg = hero_cfg.get("dark_bg", "#1a1a2e")
    return (
        f'<section style="padding:40px 28px;background:{dark_bg};">'
        f'<p style="font-size:13px;color:rgba(255,255,255,0.4);text-align:center;margin:0;">— END —</p>'
        f'</section>'
    )


def _replace_hero_footer(html: str, theme: dict, footnote_html: str = "") -> str:
    """将 HERO_FOOTER 占位符替换为脚注（可选）+ 暗色结尾，统一控制点。"""
    placeholder = '<!-- HERO_FOOTER -->'
    if placeholder not in html:
        return html
    hero_cfg = theme.get("hero", {})
    footer = _build_hero_footer(hero_cfg)
    if footnote_html:
        fn_section = f'<section style="padding:20px 28px;background:#fafafa;">{footnote_html}</section>'
        replacement = fn_section + footer
    else:
        replacement = footer
    return html.replace(placeholder, replacement)


def _wrap_card_sections(html: str, card_cfg: dict) -> str:
    """把 HTML 按 h1/h2 切分成卡片 section。card_cfg 来自主题 JSON 的 'card' 字段。"""
    card_style = (
        f'max-width:800px;width:100%;padding:25px;box-sizing:border-box;'
        f'background-color:{card_cfg["card_bg"]};'
        f'background-image:{card_cfg["card_texture"]};'
        f'background-size:{card_cfg["card_texture_size"]};'
        f'border:{card_cfg["card_border"]};'
        f'box-shadow:{card_cfg["card_shadow"]};'
        f'border-radius:{card_cfg["card_radius"]}'
    )

    # 用 h1 和 h2 作为分割点（保留 h 标签在新段的开头）
    parts = re.split(r'(?=<h[12]\s)', html)
    if not parts:
        return html

    cards = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        cards.append(f'<section style="{card_style}">{part}</section>')

    return ''.join(cards)


def inject_inline_styles(html: str, theme: dict, skip_wrapper: bool = False) -> str:
    """为每个 HTML 标签注入内联 style 属性"""
    styles = theme["styles"]

    # 构建各标签的 style 字符串
    style_map = {}
    for tag_key, props in styles.items():
        style_map[tag_key] = build_style_string(props)

    # === 1. 处理列表（微信特殊处理：ul/ol → section 模拟，支持嵌套）===
    html = convert_lists_to_sections(html, style_map)

    # === 2. 处理 callout 块 ===
    html = convert_callouts(html, style_map)

    # === 2.5 金句卡片：嵌套引用映射（mdnice multiquote 同款）===
    # >> 二级引用 → 阴影卡片；>>> 三级引用 → 居中金句卡（accent 顶线）
    # 主题可用 styles.quote_card / quote_card_center / quote_card_p 覆盖默认样式
    _q_accent = theme.get("colors", {}).get("accent", "#07C160")
    quote_card_style = style_map.get("quote_card") or (
        "background:#ffffff;box-shadow:0 4px 16px rgba(0,0,0,0.10);"
        "border-radius:10px;padding:20px;margin:24px 8px"
    )
    quote_card_center_style = style_map.get("quote_card_center") or (
        f"background:#ffffff;box-shadow:0 4px 16px rgba(0,0,0,0.10);border-radius:10px;"
        f"padding:24px 20px;margin:24px 8px;text-align:center;border-top:3px solid {_q_accent}"
    )
    quote_card_p_style = style_map.get("quote_card_p") or (
        "font-size:15px;color:#555;line-height:1.8;margin:0"
    )

    html = html.replace(
        '<section data-container="quote-card">',
        f'<section data-container="quote-card" style="{quote_card_style}">',
    )
    html = html.replace(
        '<section data-container="quote-card-center">',
        f'<section data-container="quote-card-center" style="{quote_card_center_style}">',
    )
    html = html.replace(
        '<p data-container="quote-card-p">',
        f'<p data-container="quote-card-p" style="{quote_card_p_style}">',
    )

    # === 3. 处理 blockquote 内部的 p 标签 ===
    # blockquote_prefix（可选装饰挂点）：主题定义 styles.blockquote_prefix 样式 +
    # decor.blockquote.prefix_text 文本（默认 ❝），渲染为引用块首行的真实 section
    # （mdnice 的 ::before 大引号在内联环境的等价实现）
    decor_cfg = theme.get("decor", {})

    def style_blockquote(match):
        bq_content = match.group(1)
        # blockquote 内部的 p 标签用 blockquote_p 样式
        if "blockquote_p" in style_map:
            bq_content = re.sub(
                r"<p>",
                f'<p style="{style_map["blockquote_p"]}">',
                bq_content,
            )
        bq_style = style_map.get("blockquote", "")
        prefix_html = ""
        if "blockquote_prefix" in style_map:
            q_text = decor_cfg.get("blockquote", {}).get("prefix_text", "❝")
            prefix_html = f'<section style="{style_map["blockquote_prefix"]}">{q_text}</section>'
        return f'<blockquote style="{bq_style}">{prefix_html}{bq_content}</blockquote>'

    html = re.sub(r"<blockquote>(.*?)</blockquote>", style_blockquote, html, flags=re.DOTALL)

    # === 4. 处理 pre > code（必须在单独的 code 之前）===
    def style_pre(match):
        pre_content = match.group(1)
        pre_style = style_map.get("pre", "")
        pre_code_style = style_map.get("pre_code", "")
        code_block_style = style_map.get("code_block", "")
        code_header_style = style_map.get("code_header", "")
        # 先剥出 <code> 标签，只处理内部代码文本——否则语法高亮会把
        # <code class="language-bash"> 标签本身的 class= 染色，标签碎成可见文本
        code_m = re.match(r'\s*<code([^>]*)>(.*?)</code>\s*$', pre_content, re.DOTALL)
        has_language = bool(code_m and 'class="language-' in code_m.group(1))
        inner = code_m.group(2) if code_m else pre_content
        # 保护空格：公众号编辑器会压缩连续空格，用 &nbsp; 替换
        def protect_spaces(text):
            parts = re.split(r'(<[^>]+>)', text)
            for i, part in enumerate(parts):
                if not part.startswith('<'):
                    part = part.replace(' ', '&nbsp;')
                parts[i] = part
            return ''.join(parts)
        inner = protect_spaces(inner)
        # 公众号编辑器会吃掉 pre 里的 \n，必须转成 <br> 才能保留换行
        inner = inner.replace("\n", "<br>")
        # 语法高亮：仅对有语言标记的代码块启用（避免破坏 URL 等纯文本内容）
        if has_language:
            inner = _basic_syntax_highlight(inner)
        pre_content = f'<code style="{pre_code_style}">{inner}</code>'
        # Mac 风格工具栏（红黄绿三圆点）
        dot_base = "display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:8px"
        mac_header = (
            f'<section style="{code_header_style}">'
            f'<span style="{dot_base};background:#FF5F56"></span>'
            f'<span style="{dot_base};background:#FFBD2E"></span>'
            f'<span style="{dot_base};background:#27C93F"></span>'
            f'</section>'
        )
        return (
            f'<section style="{code_block_style}">'
            f'{mac_header}'
            f'<pre style="{pre_style};overflow-x:auto;-webkit-overflow-scrolling:touch;white-space:pre">{pre_content}</pre>'
            f'</section>'
        )

    html = re.sub(r"<pre>(.*?)</pre>", style_pre, html, flags=re.DOTALL)

    # === 4.5 标题三层结构（mdnice 式装饰挂点）===
    # 主题定义 styles.{h2}_inner / {h2}_prefix / {h2}_suffix 即触发：
    #   <h2 style=外层><span style=prefix>序号/符号</span><span style=inner>标题文字</span><span style=suffix></span></h2>
    # - 外层只管布局（flex/居中/通栏线），视觉挂在 inner 上 → 色块宽度自动 = 文字宽度
    # - prefix/suffix 是伪元素的实体替身：编号标题、折角楔子、装饰符号
    # - 文本在 decor.{h2}.prefix_text / suffix_text 里配置，"{n}" 替换为该级标题的序号（01 02 …）
    for _htag in ["h1", "h2", "h3", "h4", "h5", "h6"]:
        _inner_key = f"{_htag}_inner"
        _prefix_key = f"{_htag}_prefix"
        _suffix_key = f"{_htag}_suffix"
        if not any(k in style_map for k in (_inner_key, _prefix_key, _suffix_key)):
            continue
        _h_decor = decor_cfg.get(_htag, {})
        _counter = [0]

        def _wrap_heading(match, htag=_htag, inner_key=_inner_key, prefix_key=_prefix_key,
                          suffix_key=_suffix_key, h_decor=_h_decor, counter=_counter):
            content = match.group(1)
            counter[0] += 1
            num = f"{counter[0]:02d}"
            parts = []
            if prefix_key in style_map:
                p_text = h_decor.get("prefix_text", "").replace("{n}", num)
                parts.append(f'<span style="{style_map[prefix_key]}">{p_text}</span>')
            parts.append(f'<span style="{style_map.get(inner_key, "")}">{content}</span>')
            if suffix_key in style_map:
                s_text = h_decor.get("suffix_text", "").replace("{n}", num)
                parts.append(f'<span style="{style_map[suffix_key]}">{s_text}</span>')
            outer = style_map.get(htag, "")
            return f'<{htag} style="{outer}">{"".join(parts)}</{htag}>'

        html = re.sub(rf"<{_htag}>(.*?)</{_htag}>", _wrap_heading, html, flags=re.DOTALL)

    # === 5. 普通标签注入样式 ===
    simple_tags = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "strong", "em", "a", "img", "hr", "code", "table", "th", "td"]
    for tag in simple_tags:
        if tag not in style_map:
            continue
        s = style_map[tag]
        if tag == "hr":
            html = re.sub(r"<hr\s*/?>", f'<hr style="{s}">', html)
        elif tag == "img":
            # 响应式图片：为非容器内的图片添加 width:100% 确保适配
            img_style = s
            if "width" not in img_style:
                img_style += ";width:100%"
            html = re.sub(r'<img(?!\s+style) ', f'<img style="{img_style}" ', html)
            # 已有 style 的图片（容器内）不覆盖
        elif tag == "code":
            # 只处理不在 pre 内的 code（pre 内的已经处理过了）
            # 自动补 word-break 防长 URL 溢出 section 边界
            code_s = s
            if "word-break" not in code_s:
                code_s = code_s + ";word-break:break-all" if code_s else "word-break:break-all"
            html = re.sub(r'<code(?!\s+style)>', f'<code style="{code_s}">', html)
        else:
            html = re.sub(rf"<{tag}(?!\s+style)>", f'<{tag} style="{s}">', html)
            # 带 data-container 的是围栏容器子元素，样式由 5.5 容器注入负责，这里跳过
            # （否则容器内 p 被抢注正文样式，5.5 的精确匹配全部落空——2026-06-12 修复）
            html = re.sub(rf"<{tag}(\s+(?!style|data-container)[^>]*)>", f'<{tag} style="{s}"\\1>', html)

    # === 5.1 删除线样式 ===
    html = re.sub(r'<del>', '<del style="text-decoration:line-through;color:#999">', html)

    # === 5.1.1 高亮样式 ===
    html = re.sub(r'<mark>', '<mark style="background:rgba(250,204,21,0.25);color:inherit;padding:2px 4px;border-radius:3px">', html)

    # === 5.2 标题内的 strong/em 继承标题颜色，不要用强调色 ===
    for htag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
        def fix_heading_strong(match):
            heading_html = match.group(0)
            heading_html = re.sub(
                r'<strong style="([^"]*?)color:[^;]+([^"]*?)">',
                r'<strong style="\1color:inherit\2">',
                heading_html
            )
            heading_html = re.sub(
                r'<em style="([^"]*?)color:[^;]+([^"]*?)">',
                r'<em style="\1color:inherit\2">',
                heading_html
            )
            return heading_html
        html = re.sub(rf'<{htag}\s[^>]*>.*?</{htag}>', fix_heading_strong, html, flags=re.DOTALL)

    # === 5.3 表格斑马纹（自适应：暗色主题用深色斑马，否则用亮灰）===
    def _is_dark_bg(hex_color: str) -> bool:
        h = hex_color.lstrip('#')
        if len(h) != 6:
            return False
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            return False
        return (0.299 * r + 0.587 * g + 0.114 * b) < 128

    theme_bg = theme.get("colors", {}).get("background", "#ffffff")
    zebra_bg = "rgba(255,255,255,0.04)" if _is_dark_bg(theme_bg) else "#f9f9f9"

    def add_zebra_stripes(match):
        table_html = match.group(0)
        rows = re.findall(r'<tr>(.*?)</tr>', table_html, re.DOTALL)
        result = table_html
        for i, row_content in enumerate(rows):
            old = f'<tr>{row_content}</tr>'
            if i == 0:
                continue  # 表头行跳过（th 已有样式）
            bg = f'background:{zebra_bg};' if i % 2 == 0 else ''
            new = f'<tr style="{bg}">{row_content}</tr>'
            result = result.replace(old, new, 1)
        return result
    html = re.sub(r'<table[^>]*>.*?</table>', add_zebra_stripes, html, flags=re.DOTALL)

    # === 5.4 表格手机适配（微信正文宽度约350px）===
    def adapt_table_for_mobile(match):
        table_html = match.group(0)
        # 统计列数（取第一行的 th 或 td 数量）
        first_row = re.search(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
        if not first_row:
            return table_html
        col_count = len(re.findall(r'<t[hd][\s>]', first_row.group(1)))

        # 1) 仅 4 列及以上强制 table-layout:fixed 均分列宽
        #    （2-3 列让内容自然分配，避免"名称|长说明"两列被均分挤压）
        if col_count >= 4 and 'table-layout' not in table_html:
            table_html = re.sub(
                r'<table\s+style="([^"]*)"',
                r'<table style="\1;table-layout:fixed"',
                table_html
            )

        # 2) 所有 th/td 加 word-break 防溢出
        if 'word-break' not in table_html:
            table_html = re.sub(
                r'<th\s+style="([^"]*)"',
                r'<th style="\1;word-break:break-word"',
                table_html
            )
            table_html = re.sub(
                r'<td\s+style="([^"]*)"',
                r'<td style="\1;word-break:break-word"',
                table_html
            )

        # 3) 4列及以上：缩小字号和内边距，挤进手机屏幕
        if col_count >= 4:
            table_html = re.sub(
                r'<table\s+style="([^"]*)"',
                lambda m: f'<table style="{_shrink_table_font(m.group(1))}"',
                table_html
            )
            # th/td padding 缩到最小
            table_html = re.sub(
                r'(<t[hd]\s+style="[^"]*?)padding:[^;"]+',
                r'\1padding:6px 4px',
                table_html
            )

        return table_html

    def _shrink_table_font(style_str):
        """4+列表格：字号强制 13px"""
        if 'font-size' in style_str:
            return re.sub(r'font-size:[^;]+', 'font-size:13px', style_str)
        return style_str + ';font-size:13px'

    html = re.sub(r'<table[^>]*>.*?</table>', adapt_table_for_mobile, html, flags=re.DOTALL)

    # === 5.5 处理围栏容器内联样式 ===
    html = _inject_container_styles(html, theme)

    # === 6. 处理脚注占位符样式（使用 UUID 安全占位符）===
    for key, placeholder in FOOTNOTE_PLACEHOLDERS.items():
        if key in style_map:
            html = html.replace(placeholder, style_map[key])

    # === 7. 处理图片包裹容器 ===
    if "img_wrapper" in style_map:
        html = re.sub(
            r'<section data-role="img-wrapper">',
            f'<section data-role="img-wrapper" style="{style_map["img_wrapper"]}">',
            html,
        )

    # === 8. 特殊布局（仅主体 HTML，脚注等片段跳过）===
    if not skip_wrapper:
        if theme.get("layout") == "hero" and "hero" in theme:
            html = _wrap_hero_sections(html, theme["hero"])
        elif theme.get("layout") == "timeline" and "timeline" in theme:
            html = _wrap_timeline_sections(html, theme["timeline"])
        elif theme.get("layout") == "card" and "card" in theme:
            html = _wrap_card_sections(html, theme["card"])

    # === 8.1 处理 wrapper（整体背景色，用于 dark/retro 等主题）===
    if "wrapper" in style_map and not skip_wrapper:
        if theme.get("layout") == "hero":
            # Hero 布局：不加额外 wrapper，各 section 自带背景
            pass
        elif theme.get("layout") == "card":
            # 卡片主题：wrapper 用 flex 列布局 + 卡片间距
            card_bg = theme["card"]["bg"]
            wrapper_s = f'padding:40px 10px;background-color:{card_bg};display:flex;flex-direction:column;align-items:center;gap:40px'
            html = f'<section style="{wrapper_s}">{html}</section>'
        else:
            html = f'<section style="{style_map["wrapper"]}">{html}</section>'

    # === 9. 注入微信深色模式属性（自动补全缺失标签）===
    dark_mode = _auto_dark_mode(theme)
    if dark_mode:
        html = inject_dark_mode_attrs(html, dark_mode, style_map)

    # === 10. blockquote → section（防新版编辑器重写）===
    # 2024-11 起微信灰度的新编辑器会把 <blockquote> 重写成自家引用格式、剥掉自定义样式
    # （doocs/md #447）。样式已全部内联，标签语义可以舍弃，统一换成 section 保平安
    html = html.replace("<blockquote ", '<section data-role="blockquote" ')
    html = html.replace("<blockquote>", '<section data-role="blockquote">')
    html = html.replace("</blockquote>", "</section>")

    return html


def convert_lists_to_sections(html: str, style_map: dict, depth: int = 0) -> str:
    """把 ul/ol 列表转为 section 模拟（微信兼容），支持嵌套"""
    wrapper_style = style_map.get("list_wrapper", "")
    row_style = style_map.get("list_item_row", "")
    bullet_style = style_map.get("list_item_bullet", "")
    text_style = style_map.get("list_item_text", "")
    ol_bullet_style = style_map.get("ol_item_bullet", bullet_style)

    # 嵌套缩进（display:block + box-sizing 确保微信不压缩 padding）
    indent = f"padding-left:{16 * depth}px;display:block;box-sizing:border-box;" if depth > 0 else ""

    def process_list_item(item_html: str, bullet: str, bullet_s: str) -> str:
        """处理单个 li，可能包含嵌套的 ul/ol"""
        # 检查是否有嵌套列表
        nested_ul = re.search(r'<ul>(.*?)</ul>', item_html, re.DOTALL)
        nested_ol = re.search(r'<ol>(.*?)</ol>', item_html, re.DOTALL)

        # 提取主文本（去掉嵌套列表和 p 标签）
        main_text = item_html
        if nested_ul:
            main_text = item_html[:nested_ul.start()]
        elif nested_ol:
            main_text = item_html[:nested_ol.start()]
        main_text = re.sub(r"</?p>", "", main_text).strip()

        # 当前项
        result = (
            f'<section style="{row_style}{indent}">'
            f'<span style="{bullet_s}">{bullet}</span>'
            f'<span style="{text_style}">{main_text}</span>'
            f"</section>"
        )

        # 递归处理嵌套列表
        if nested_ul:
            nested_html = f'<ul>{nested_ul.group(1)}</ul>'
            result += convert_lists_to_sections(nested_html, style_map, depth + 1)
        elif nested_ol:
            nested_html = f'<ol>{nested_ol.group(1)}</ol>'
            result += convert_lists_to_sections(nested_html, style_map, depth + 1)

        return result

    def replace_ul(match):
        items = re.findall(r"<li>(.*?)</li>", match.group(0), re.DOTALL)
        rows = []
        for item in items:
            rows.append(process_list_item(item, "•", bullet_style))
        wrap = f'{wrapper_style}{indent}' if indent else wrapper_style
        return f'<section style="{wrap}">{"".join(rows)}</section>'

    def replace_ol(match):
        items = re.findall(r"<li>(.*?)</li>", match.group(0), re.DOTALL)
        rows = []
        for idx, item in enumerate(items, 1):
            rows.append(process_list_item(item, str(idx), ol_bullet_style))
        wrap = f'{wrapper_style}{indent}' if indent else wrapper_style
        return f'<section style="{wrap}">{"".join(rows)}</section>'

    html = re.sub(r"<ul>.*?</ul>", replace_ul, html, flags=re.DOTALL)
    html = re.sub(r"<ol>.*?</ol>", replace_ol, html, flags=re.DOTALL)
    return html


def convert_callouts(html: str, style_map: dict) -> str:
    """转换 callout 块为带样式的 HTML（支持多类型颜色）"""
    callout_style = style_map.get("callout", "")
    title_style = style_map.get("callout_title", "")
    content_style = style_map.get("callout_content", "")

    def replace_callout(match):
        inner = match.group(1)
        data_type = match.group(0)
        # 提取 data-type
        type_match = re.search(r'data-type="(\w+)"', data_type)
        callout_type = type_match.group(1) if type_match else "callout"

        # 判断是否有类型专属颜色
        type_colors = CALLOUT_TYPE_COLORS.get(callout_type)

        # 构建 callout 容器样式（可能覆盖边框和背景色）
        final_callout_style = callout_style
        if type_colors is not None:
            # 覆盖 border-left 颜色和 background 颜色
            final_callout_style = re.sub(
                r'border-left:[^;]+', f'border-left:4px solid {type_colors["border"]}',
                final_callout_style
            )
            # 如果原样式有 background，替换；否则追加
            if 'background' in final_callout_style:
                final_callout_style = re.sub(
                    r'background[^;]*:[^;]+', f'background:{type_colors["bg"]}',
                    final_callout_style
                )
            else:
                final_callout_style += f';background:{type_colors["bg"]}'

        # 提取标题和内容
        title_match_inner = re.search(r'<p class="callout-title">(.*?)</p>', inner)
        content_match = re.search(r'<p class="callout-content">(.*?)</p>', inner, re.DOTALL)

        # 确保子元素继承字号行高（微信可能不自动继承）
        if 'font-size:inherit' not in final_callout_style:
            final_callout_style += ';font-size:inherit;line-height:inherit'
        result = f'<section style="{final_callout_style}">'
        if title_match_inner and title_match_inner.group(1):
            title_text = title_match_inner.group(1)
            # 类型专属 icon 前缀 + 标题颜色跟随 callout 类型
            final_title_style = title_style
            if type_colors is not None:
                title_text = f'{type_colors["icon"]} {title_text}'
                final_title_style = re.sub(r'color:[^;]+', f'color:{type_colors["border"]}', final_title_style)
            result += f'<p style="{final_title_style}">{title_text}</p>'
        if content_match:
            result += f'<p style="{content_style}">{content_match.group(1)}</p>'
        result += "</section>"
        return result

    return re.sub(r'<div class="callout"[^>]*>(.*?)</div>', replace_callout, html, flags=re.DOTALL)


# ── 预览 HTML 生成 ──────────────────────────────────────────────────────
def generate_preview(article_html: str, footnote_html: str, theme: dict,
                     title: str, word_count: int, output_path: Path):
    """生成浏览器预览 HTML 文件"""
    template_path = TEMPLATE_DIR / "preview.html"
    template = template_path.read_text(encoding="utf-8")

    # Hero 布局：通过占位符统一插入脚注 + 暗色 footer
    extra_css = ""
    if theme.get("layout") == "hero":
        extra_css = '<style>.article-content { padding: 0 !important; }</style>'
        full_html = _replace_hero_footer(article_html, theme, footnote_html)
    else:
        # 普通布局：脚注追加到末尾
        full_html = article_html
        if footnote_html:
            full_html += "\n" + footnote_html

    preview_html = (
        template
        .replace("{{TITLE}}", title)
        .replace("{{THEME_NAME}}", theme.get("name", ""))
        .replace("{{WORD_COUNT}}", f"{word_count:,}")
        .replace("{{ARTICLE_HTML}}", extra_css + full_html)
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(preview_html, encoding="utf-8")
    return output_path


def convert_image_captions(html: str) -> str:
    """将图片后紧跟的斜体段落转为图说样式"""
    caption_style = "text-align:center;font-size:13px;color:#999999;margin-top:-8px;margin-bottom:16px;font-style:normal"
    # 匹配 img wrapper (</section>) 后面紧跟的 <p><em>xxx</em></p>
    html = re.sub(
        r'(</section>\s*)<p[^>]*><em>(.*?)</em></p>',
        rf'\1<p style="{caption_style}">\2</p>',
        html
    )
    # 同时匹配 </p>（CDN/外链图片）后面的斜体图说
    html = re.sub(
        r'(</p>\s*)<p[^>]*><em>(.*?)</em></p>',
        rf'\1<p style="{caption_style}">\2</p>',
        html
    )
    return html


def truncate_html_preview(html: str, max_p_tags: int = 12) -> str:
    """截取 HTML 前 N 个 </p> 之前的内容作为预览"""
    count = 0
    pos = 0
    while count < max_p_tags:
        idx = html.find("</p>", pos)
        if idx == -1:
            break
        pos = idx + len("</p>")
        count += 1
    if pos > 0:
        return html[:pos]
    return html[:2000]


def _apply_font_size(html: str, size: int) -> str:
    """把正文默认字号(15px)替换为指定字号"""
    html = re.sub(r'font-size:\s*15px', f'font-size: {size}px', html)
    if size == 16:
        html = re.sub(r'line-height:\s*1\.8\b', 'line-height: 1.75', html)
    return html


def _render_single_theme(tid, theme_data, gallery_html, gallery_footnote):
    """渲染单个主题（用于并行 gallery）"""
    rendered = inject_inline_styles(gallery_html, theme_data)
    rendered = convert_image_captions(rendered)
    if theme_data.get("layout") == "hero":
        # Hero 布局：脚注通过占位符统一插入
        fn_html = ""
        if gallery_footnote:
            fn_html = inject_inline_styles(gallery_footnote, theme_data, skip_wrapper=True)
        rendered = _replace_hero_footer(rendered, theme_data, fn_html)
    elif gallery_footnote:
        fn_rendered = inject_inline_styles(gallery_footnote, theme_data, skip_wrapper=True)
        rendered += "\n" + fn_rendered
    return tid, rendered


def generate_gallery(rendered_map: dict, theme_map: dict,
                     theme_ids: list, title: str, word_count: int,
                     output_dir: Path, recommended: list = None):
    """生成主题画廊页面（单预览区 + 切换按钮模式）"""
    if recommended is None:
        recommended = []
    template_path = TEMPLATE_DIR / "gallery.html"
    template = template_path.read_text(encoding="utf-8")

    default_theme = theme_ids[0] if theme_ids else ""

    # 生成 THEME_BUTTONS（带分组标签）
    # 2026-06-12 合并去重后的分类（与 GALLERY_THEMES 同步，34 款）
    GROUPS = [
        ("新主题候选", ["data-report", "interview", "notion-doc", "glass-light"]),
        ("纸系·Kami", ["kami-ink"]),
        ("新做精选", ["claude-scroll", "swiss-grid", "pastel-dream", "brutalism-raw", "blueprint", "academic-paper", "industrial", "magazine-serif"]),
        ("特色布局", ["hero-purple", "dark-ocean", "timeline-green", "zen-minimal"]),
        ("卡片系列", ["warm-card", "apple-gradient", "cyber-neon"]),
        ("深度长文", ["newspaper", "magazine", "ink", "coffee-house"]),
        ("科技产品", ["bytedance", "github", "sspai", "midnight"]),
        ("文艺随笔", ["terracotta", "mint-fresh"]),
        ("活力动态", ["sports", "bauhaus", "chinese", "wechat-native"]),
        ("模板布局", ["minimal-gold", "focus-blue", "elegant-green", "bold-blue"]),
    ]
    buttons_html = ""
    btn_index = 0
    for group_name, group_ids in GROUPS:
        group_tids = [t for t in group_ids if t in theme_ids]
        if not group_tids:
            continue
        buttons_html += f'<div class="theme-group"><span class="group-label">{group_name}</span>'
        for tid in group_tids:
            theme = theme_map[tid]
            accent = theme.get("colors", {}).get("accent", "#333")
            active = " active" if btn_index == 0 else ""
            is_recommended = " recommended" if tid in recommended else ""
            name = theme.get("name", tid)
            rec_label = '<span class="rec-badge">推荐</span>' if tid in recommended else ""
            buttons_html += (
                f'<button class="theme-btn{active}{is_recommended}" data-theme="{tid}" '
                f"onclick=\"switchTheme('{tid}')\">"
                f'<span class="theme-dot" style="background:{accent}"></span>'
                f'{name}{rec_label}</button>'
            )
            btn_index += 1
        buttons_html += '</div>\n'

    # 生成 THEME_PREVIEWS
    previews_html = ""
    for i, tid in enumerate(theme_ids):
        display = "block" if i == 0 else "none"
        previews_html += (
            f'<div class="theme-preview" data-theme="{tid}" '
            f'style="display:{display}">{rendered_map[tid]}</div>\n'
        )

    gallery_html = (
        template
        .replace("{{TITLE}}", title)
        .replace("{{WORD_COUNT}}", f"{word_count:,}")
        .replace("{{THEME_BUTTONS}}", buttons_html)
        .replace("{{THEME_PREVIEWS}}", previews_html)
        .replace("{{DEFAULT_THEME}}", default_theme)
    )

    # 写入选中主题到临时文件（默认第一个）
    tmp_dir = Path("/tmp/wechat-format")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "selected-theme.txt").write_text(default_theme, encoding="utf-8")

    output_path = output_dir / "gallery.html"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(gallery_html, encoding="utf-8")
    return output_path


def format_for_output(content: str, input_path: Path, theme: dict,
                      output_dir: Path, vault_root: Path,
                      output_format: str = "wechat") -> dict:
    """统一格式化入口，支持多种输出格式

    Args:
        output_format: "wechat" (默认，全套微信兼容处理)
                      "html" (标准 HTML，保留 class，不内联样式)
                      "plain" (纯文本 + 基本 HTML 结构)

    Returns:
        dict with keys: html, footnote_html, title, word_count
    """
    title = extract_title(content, input_path)
    word_count = count_words(content)

    # 通用预处理
    content = strip_frontmatter(content)
    # frontmatter title 但无 H1 → 注入 H1，让 hero 等布局能识别标题区
    if title and not re.search(r'^#\s+', content, re.MULTILINE):
        content = f"# {title}\n\n{content}"
    content = fix_cjk_spacing(content)
    content = fix_cjk_bold_punctuation(content)
    content = _auto_detect_byline(content)
    content = process_callouts(content)
    content = process_manual_footnotes(content)
    content = process_fenced_containers(content)
    content = re.sub(r'~~(.+?)~~', r'<del>\1</del>', content)
    content = re.sub(r'%%.*?%%', '', content)  # Obsidian 注释（配对）
    content = re.sub(r'%%', '', content)  # 清理落单的 %%
    content = re.sub(r'==(.*?)==', r'<mark>\1</mark>', content)  # Obsidian 高亮
    content = re.sub(r'- \[x\]\s*', '- ✅ ', content)  # 任务列表：已完成
    content = re.sub(r'- \[ \]\s*', '- ⬜ ', content)  # 任务列表：未完成

    output_dir.mkdir(parents=True, exist_ok=True)
    content = convert_wikilinks(content, vault_root, output_dir)
    content = copy_markdown_images(content, input_path.parent, output_dir)

    html = md_to_html(content)

    if output_format == "plain":
        # 纯 HTML，不做脚注转换和样式注入
        return {
            "html": html,
            "footnote_html": "",
            "title": title,
            "word_count": word_count,
        }

    # 外链 → 脚注
    html, footnote_html = extract_links_as_footnotes(html)

    if output_format == "html":
        # 标准 HTML，脚注转换但不内联样式
        return {
            "html": html,
            "footnote_html": footnote_html,
            "title": title,
            "word_count": word_count,
        }

    # wechat: 全套微信兼容处理
    html = inject_inline_styles(html, theme)
    if footnote_html:
        footnote_html = inject_inline_styles(footnote_html, theme, skip_wrapper=True)
    html = convert_image_captions(html)
    if footnote_html:
        footnote_html = convert_image_captions(footnote_html)

    return {
        "html": html,
        "footnote_html": footnote_html,
        "title": title,
        "word_count": word_count,
    }


# ── 主流程 ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="微信公众号文章排版工具")
    parser.add_argument("--input", "-i", required=True, help="输入 Markdown 文件路径")
    parser.add_argument("--theme", "-t", default=DEFAULT_THEME, help=f"主题名称（默认: {DEFAULT_THEME}）")
    parser.add_argument("--vault-root", default=str(VAULT_ROOT), help="Obsidian Vault 根目录")
    parser.add_argument("--output", "-o", default=str(OUTPUT_DIR), help="输出目录")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--gallery", action="store_true", help="主题画廊模式：预览多个主题供选择")
    parser.add_argument("--recommend", nargs="*", default=[], help="推荐的主题ID列表（gallery中高亮显示）")
    parser.add_argument("--format", choices=["wechat", "html", "plain"], default="wechat",
                        help="输出格式: wechat(默认), html(标准HTML), plain(纯HTML)")
    parser.add_argument("--smart", action="store_true",
                        help="AI 语义增强：自动分析文章并添加排版标记（需 config.json 配置 smart_api）")
    parser.add_argument("--font-size", type=int, default=None,
                        help="正文字号（默认15px），如 --font-size 16")
    args = parser.parse_args()

    input_path = Path(args.input)
    vault_root = Path(args.vault_root)
    output_base = Path(args.output)
    theme_name = args.theme

    # 每篇文章一个子目录: 公众号排版/2026-02-26-文章名/
    file_stem = re.sub(r"-(公众号|小红书|微博)$", "", input_path.stem)
    output_dir = output_base / file_stem

    # 验证输入文件
    if not input_path.exists():
        print(f"错误: 文件不存在 - {input_path}")
        sys.exit(1)

    # 加载主题
    theme = load_theme(theme_name)

    print(f"主题: {theme['name']} ({theme_name})")
    print(f"输入: {input_path}")

    # 读取文章
    content = input_path.read_text(encoding="utf-8")

    # --smart: AI 语义增强（在所有处理之前）
    if args.smart:
        config_path = SCRIPT_DIR.parent / "config.json"
        content = smart_enhance_markdown(content, config_path)

    title = extract_title(content, input_path)
    word_count = count_words(content)
    print(f"标题: {title}")
    print(f"字数: {word_count:,}")

    # 非微信格式：简单输出
    if args.format != "wechat":
        result = format_for_output(content, input_path, theme, output_dir, vault_root, args.format)
        out_path = output_dir / f"article.{args.format}.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_html = result["html"]
        if result["footnote_html"]:
            out_html += "\n" + result["footnote_html"]
        out_path.write_text(out_html, encoding="utf-8")
        print(f"\n输出: {out_path}")
        return

    # 处理流程
    content = strip_frontmatter(content)
    # frontmatter title 但无 H1 → 注入 H1，让 hero 等布局能识别标题区
    if title and not re.search(r'^#\s+', content, re.MULTILINE):
        content = f"# {title}\n\n{content}"
    content = _auto_detect_byline(content)
    content = process_callouts(content)
    content = process_manual_footnotes(content)
    content = process_fenced_containers(content)
    content = re.sub(r'~~(.+?)~~', r'<del>\1</del>', content)
    content = re.sub(r'%%.*?%%', '', content)  # Obsidian 注释（配对）
    content = re.sub(r'%%', '', content)  # 清理落单的 %%
    content = re.sub(r'==(.*?)==', r'<mark>\1</mark>', content)  # Obsidian 高亮
    content = re.sub(r'- \[x\]\s*', '- ✅ ', content)  # 任务列表：已完成
    content = re.sub(r'- \[ \]\s*', '- ⬜ ', content)  # 任务列表：未完成

    output_dir.mkdir(parents=True, exist_ok=True)
    content = convert_wikilinks(content, vault_root, output_dir)
    content = copy_markdown_images(content, input_path.parent, output_dir)

    html = md_to_html(content)
    html, footnote_html = extract_links_as_footnotes(html)

    # ── Gallery 模式：并行渲染多主题 ──
    if args.gallery:
        # 先跑主题字号 lint(非阻断,只警告)
        import subprocess
        lint_script = Path(__file__).parent / "theme_lint.py"
        if lint_script.exists():
            try:
                r = subprocess.run(
                    ["python3", str(lint_script)],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode != 0 and r.stdout.strip():
                    print("[theme-lint] ⚠ 检测到字号问题(不阻断渲染):")
                    for line in r.stdout.strip().split("\n"):
                        print(f"  {line}")
                    print()
            except Exception:
                pass

        gallery_html = html
        gallery_footnote = footnote_html

        theme_map = {}
        for tid in GALLERY_THEMES:
            tp = THEMES_DIR / f"{tid}.json"
            if tp.exists():
                with open(tp, encoding="utf-8") as f:
                    theme_map[tid] = json.load(f)

        gallery_theme_ids = [tid for tid in GALLERY_THEMES if tid in theme_map]

        if not gallery_theme_ids:
            print("错误: 没有找到任何可用的画廊主题")
            sys.exit(1)

        print(f"\n画廊模式: 并行渲染 {len(gallery_theme_ids)} 个主题...")
        rendered_map = {}

        # 并行渲染（线程池）
        with ThreadPoolExecutor(max_workers=min(8, len(gallery_theme_ids))) as executor:
            futures = {
                executor.submit(
                    _render_single_theme, tid, theme_map[tid],
                    gallery_html, gallery_footnote
                ): tid
                for tid in gallery_theme_ids
            }
            for future in as_completed(futures):
                tid, rendered = future.result()
                rendered_map[tid] = rendered
                print(f"  ✓ {theme_map[tid].get('name', tid)} ({tid})")

        gallery_path = generate_gallery(
            rendered_map, theme_map, gallery_theme_ids,
            title, word_count, output_dir,
            recommended=args.recommend
        )
        print(f"\n画廊页面: {gallery_path}")

        if AUTO_OPEN and not args.no_open:
            webbrowser.open(f"file://{gallery_path}")
            print("已在浏览器中打开画廊")

        print(f"\n完成! 选中主题后点「用这个风格排版」即可复制到剪贴板。")
        print(f"选中的主题 ID 会写入 /tmp/wechat-format/selected-theme.txt")
        return

    # ── 单主题模式 ──
    html = inject_inline_styles(html, theme)
    if footnote_html:
        footnote_html = inject_inline_styles(footnote_html, theme, skip_wrapper=True)

    html = convert_image_captions(html)
    if footnote_html:
        footnote_html = convert_image_captions(footnote_html)

    # --font-size: 替换正文字号
    if args.font_size and args.font_size != 15:
        html = _apply_font_size(html, args.font_size)
        if footnote_html:
            footnote_html = _apply_font_size(footnote_html, args.font_size)

    # 保存纯文章 HTML（hero 布局走统一占位符替换）
    if theme.get("layout") == "hero":
        full_article = _replace_hero_footer(html, theme, footnote_html)
    else:
        full_article = html
        if footnote_html:
            full_article += "\n" + footnote_html
    article_path = output_dir / "article.html"
    article_path.write_text(full_article, encoding="utf-8")

    # 保存预览 HTML
    preview_path = output_dir / "preview.html"
    generate_preview(html, footnote_html, theme, title, word_count, preview_path)
    print(f"\n排版成品: {preview_path}")

    if AUTO_OPEN and not args.no_open:
        webbrowser.open(f"file://{preview_path}")
        print("已在浏览器中打开预览")

    print("\n完成! 在浏览器中点击「复制到微信」按钮，然后粘贴到公众号后台即可。")


if __name__ == "__main__":
    main()
