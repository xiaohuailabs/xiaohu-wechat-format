# xiaohu-wechat-format

一个同时适用于 **Claude**、**Codex** 和命令行的公众号排版工具。

它会把 Markdown 转成公众号兼容的内联 HTML，打开主题画廊让你直接挑样式，支持可调的极简主题，也支持后续推送到公众号草稿箱。

**[English README](README.md)**

![画廊预览](docs/gallery-preview.png)

## 功能

1. Markdown -> 公众号兼容 HTML。
2. 基于真实文章内容的主题画廊。
3. 新增可调极简主题 `minimal-flex`。
4. 同时保存 `selected-theme.txt` 和 `selected-style.json`。
5. 可选发布到公众号草稿箱。

## 安装

### Claude

```bash
cd ~/.claude/skills/
git clone https://github.com/xiaohuailabs/xiaohu-wechat-format.git
cp xiaohu-wechat-format/config.example.json xiaohu-wechat-format/config.json
pip3 install markdown requests
```

### Codex / 命令行

```bash
git clone https://github.com/xiaohuailabs/xiaohu-wechat-format.git
cd xiaohu-wechat-format
cp config.example.json config.json
pip3 install markdown requests
```

Codex 有两种常见用法：

1. 直接把仓库 clone 到当前工作区，然后调用脚本。
2. 直接把仓库 URL 或本地仓库路径发给 Codex，让它调用 `scripts/format.py` 或 `scripts/publish.py`。

## 配置

编辑 `config.json`：

```json
{
  "output_dir": "./wechat-output-cache",
  "vault_root": "/path/to/your/obsidian/vault",
  "settings": {
    "default_theme": "newspaper",
    "auto_open_browser": true
  },
  "wechat": {
    "app_id": "你的 AppID",
    "app_secret": "你的 AppSecret",
    "author": "作者名"
  },
  "cover": {
    "output_dir": "~/Documents/covers",
    "image_generation_script": ""
  }
}
```

- 现在默认会在源 Markdown 同级目录下创建 `wechat output/`。
- `output_dir` 保留为旧流程兼容用的兜底目录。
- `wechat` 只在发布时需要。
- `cover` 只在生成封面时需要。

## 排版文章

### 打开主题画廊

```bash
python3 scripts/format.py --input article.md --gallery
```

默认输出结构：

```text
article.md
wechat output/
  gallery.html
  article.html
  preview.html
  images/
```

画廊页面只负责预览和选样式，不会直接改你的原文。复制到公众号后台之后，你仍然可以继续微调文字。

### 直接排版

```bash
python3 scripts/format.py --input article.md --theme newspaper
```

### 可调极简主题

```bash
python3 scripts/format.py \
  --input article.md \
  --theme minimal-flex \
  --accent blue \
  --heading-align center \
  --divider-style fade-short \
  --strong-style highlight
```

`minimal-flex` 支持：

- `--accent`：`black`、`gray`、`blue`、`green`、`red`、`navy`、`gold`
- `--heading-align`：`left`、`center`、`right`
- `--divider-style`：`solid-full`、`solid-short`、`soft-full`、`fade-short`、`bold-short`、`none`
- `--strong-style`：`color`、`highlight`

从 `#` 到 `#####` 的标题都会跟随对齐设置。

## 样式选择结果

画廊选择结果会写入系统临时目录：

- Windows 示例：`%TEMP%\wechat-format\selected-theme.txt`
- Windows 示例：`%TEMP%\wechat-format\selected-style.json`
- macOS / Linux 示例：`/tmp/wechat-format/selected-theme.txt`
- macOS / Linux 示例：`/tmp/wechat-format/selected-style.json`

后续流程优先读 `selected-style.json`，同时继续兼容 `selected-theme.txt`。

## 发布到公众号草稿箱

```bash
python3 scripts/publish.py --dir "/path/to/article-folder/wechat output" --cover cover.jpg
```

或者直接从 Markdown 一步到位：

```bash
python3 scripts/publish.py --input article.md
```

配合 `minimal-flex`：

```bash
python3 scripts/publish.py \
  --input article.md \
  --theme minimal-flex \
  --accent green \
  --heading-align right \
  --divider-style none \
  --strong-style color
```

## Windows 说明

- 在 Windows 下不要再依赖 `/tmp/...` 这种路径。
- `format.py --gallery` 现在会输出真实的本地目录和 `http://127.0.0.1:.../gallery.html` 地址。
- 单主题预览现在会输出准确的 `file:///.../preview.html` 地址。
- 如果本地 gallery 服务没启动成功，脚本会自动回退到直接打开生成出的 `gallery.html` 文件。

## 主题说明

- 旧主题 ID 保持兼容。
- 画廊里的极简组现在只保留一个 `minimal-flex` 入口。
- 画廊不再额外显示“推荐”小角标，默认样式改成更克制的弱提示。
- 旧的 `minimal-*` 主题仍然可以通过命令行直接调用。

## 容器语法

```markdown
:::dialogue[对话标题]
Alice: Hello there
Bob: Hi, how are you?
:::

:::gallery[截图]
![](img1.jpg)
![](img2.jpg)
![](img3.jpg)
:::

> [!important] 核心观点
> 这里是重点内容
```

## 封面流程

```bash
python3 scripts/generate.py \
  --config cover/config.json \
  --prompt-file prompt.md \
  --out cover.jpg
```

封面流程见 `cover/SKILL.md`。

## 依赖

- Python 3
- `markdown`
- `requests`，仅发布时需要

## License

MIT
