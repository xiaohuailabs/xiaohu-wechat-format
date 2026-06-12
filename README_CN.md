# xiaohu-wechat-format

Claude Code 公众号完整发布管线：**排版** → **封面**（可选）→ **推送**，一句话搞定。

**[English README](README.md)**

![画廊预览](docs/gallery-preview.png)

## 功能

- **排版引擎**：Markdown 转微信公众号兼容的内联样式 HTML
- **85 套主题**：含卡片 / 时间线 / hero 特色布局，画廊精选 34 款分 10 类（新主题候选 / 纸系·Kami / 新做精选 / 特色布局 / 卡片系列 / 深度长文 / 科技产品 / 文艺随笔 / 活力动态 / 模板布局）
- **AI 内容增强**：自动识别对话体、金句、连续图片，套用 dialogue / callout / gallery 容器；也支持手写 `:::` 容器语法（byline / stat / gallery / longimage / dialogue）
- **CJK 排版修复**：中英文自动加空格、加粗标点自动移出标记、中文标点自动规范化（zh_punctuation_fix.py）
- **图片处理**：自动处理 Obsidian `![[image]]` 和标准 Markdown `![](image)` 引用
- **外链转脚注**：微信不支持外链，自动转文末脚注
- **封面图生成**：内置 Gemini API 生图脚本 + 提示词模板，一步出图
- **一键发布**：自动上传图片到微信 CDN + 推送到草稿箱
- **主题画廊**：浏览器中用真实文章实时预览，字号（15/16/17px）和字体（默认 / 衬线 / 宋体 / 楷体 / 圆体 / 仿宋）即时切换，选好一键回传 Claude Code 或直接复制进公众号后台

## 安装

```bash
cd ~/.claude/skills/
git clone https://github.com/xiaohuailabs/xiaohu-wechat-format.git
cp xiaohu-wechat-format/config.example.json xiaohu-wechat-format/config.json
pip3 install markdown requests
```

## 配置

编辑 `config.json`：

```json
{
  "output_dir": "/tmp/wechat-format",
  "vault_root": "/path/to/your/obsidian/vault",
  "settings": {
    "default_theme": "newspaper",
    "auto_open_browser": true,
    "header_author_label": ""
  },
  "wechat": {
    "app_id": "你的AppID",
    "app_secret": "你的AppSecret",
    "author": "作者名"
  },
  "cover": {
    "output_dir": "~/Documents/covers",
    "image_generation_script": ""
  },
  "smart_api": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "你的API密钥",
    "model": "gpt-4o-mini"
  }
}
```

- `wechat` 部分仅推送时需要，纯排版可以不填
- `header_author_label`：卡片/时间线/hero 布局标题区显示的署名，留空则回退到 `wechat.author`，都没有就不显示
- `smart_api`：仅 `--smart` AI 语义增强需要，任意 OpenAI 兼容接口
- `cover` 部分仅生成封面时需要（详见下方封面配置）
- 获取 AppID 和 AppSecret：微信公众号后台 → 设置与开发 → 基本配置
- **重要**：需要把你的公网 IP 加到公众号后台的 IP 白名单里，否则 API 调用会报 40164 错误

## 使用

在 Claude Code 里直接说：

```
排版这篇文章 /path/to/article.md
```

### 主题画廊（推荐）

```bash
python3 scripts/format.py --input article.md --gallery --recommend newspaper magazine ink
```

在浏览器中用真实文章预览 34 个核心主题（可切字号和字体），选好后点「确认」回传 Claude Code，或直接复制进公众号后台。

### 指定主题排版

```bash
python3 scripts/format.py --input article.md --theme newspaper
```

### 推送到公众号

```bash
python3 scripts/publish.py --dir /tmp/wechat-format/article-name/ --cover cover.jpg
```

## 主题一览

### 独立风格（9 个）

| 主题 | 命令值 | 风格 |
|------|--------|------|
| 赤陶 | terracotta | 暖橙色，满底圆角标题 |
| 字节蓝 | bytedance | 蓝青渐变，科技现代 |
| 中国风 | chinese | 朱砂红，古典雅致 |
| 报纸 | newspaper | 纽约时报风，严肃深度 |
| GitHub | github | 开发者风，浅色代码块 |
| 少数派 | sspai | 中文科技媒体红 |
| 包豪斯 | bauhaus | 红蓝黄三原色，先锋几何 |
| 墨韵 | ink | 纯黑水墨，极简留白 |
| 暗夜 | midnight | 深色底+霓虹色 |

### 精选风格（7 个）

| 主题 | 命令值 | 风格 |
|------|--------|------|
| 运动 | sports | 渐变色带，活力动感 |
| 薄荷 | mint-fresh | 薄荷绿，清爽 |
| 日落 | sunset-amber | 琥珀暖调 |
| 薰衣草 | lavender-dream | 紫色梦幻 |
| 咖啡 | coffee-house | 棕色暖调 |
| 微信原生 | wechat-native | 微信绿 |
| 杂志 | magazine | 超大留白 |

### 模板系列（14 个）

四种布局（简约 / 聚焦 / 精致 / 醒目）× 多种配色（金 / 蓝 / 红 / 绿 / 藏青 / 灰）

## 容器语法

文章中可使用以下容器增强排版：

```markdown
:::dialogue[对话标题]
张三：你好
李四：你好啊
:::

:::gallery[图片标题]
![](img1.jpg)
![](img2.jpg)
![](img3.jpg)
:::

> [!important] 核心观点
> 这里是重点内容

> [!tip] 小技巧
> 实用提示
```

## 封面图生成

仓库自带封面图生成器（`cover/` 目录），调用 Gemini Image API（或兼容的第三方网关）生成公众号封面图（2.35:1 Notion 插画风格）。

### 配置

1. 复制 `cover/config.example.json` → `cover/config.json`
2. 填写 API 信息：

```json
{
  "output_dir": "~/Documents/covers",
  "settings": {
    "base_url": "https://你的API地址/v1",
    "model": "gemini-3-pro-image-preview"
  },
  "secrets": {
    "api_key": "你的API密钥"
  }
}
```

3. 生成封面：

```bash
python3 scripts/generate.py \
  --config cover/config.json \
  --prompt-file prompt.md \
  --out cover.jpg
```

或者在 Claude Code 里直接说：`给这篇文章配个封面`

完整提示词模板和工作流详见 `cover/SKILL.md`。

## 自定义主题

在 `themes/` 目录下创建 JSON 文件。参考 `themes/newspaper.json`。

## 依赖

- Python 3
- `markdown` 库
- `requests` 库（推送到公众号时需要）

## License

MIT
