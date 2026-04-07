# xiaohu-wechat-format

A WeChat Official Account formatting toolkit for **Claude**, **Codex**, and direct CLI use.

It turns Markdown into WeChat-compatible inline HTML, opens a browser gallery for theme selection, supports adjustable minimal styling, and can optionally publish to WeChat drafts.

**[中文说明](README_CN.md)**

![Gallery Preview](docs/gallery-preview.png)

## What It Does

1. Markdown -> WeChat HTML with inline styles.
2. Theme gallery based on your real article.
3. A new adjustable minimal theme: `minimal-flex`.
4. Structured style persistence via `selected-theme.txt` and `selected-style.json`.
5. Optional publish flow for WeChat drafts.

## Install

### Claude

```bash
cd ~/.claude/skills/
git clone https://github.com/xiaohuailabs/xiaohu-wechat-format.git
cp xiaohu-wechat-format/config.example.json xiaohu-wechat-format/config.json
pip3 install markdown requests
```

### Codex / CLI

```bash
git clone https://github.com/xiaohuailabs/xiaohu-wechat-format.git
cd xiaohu-wechat-format
cp config.example.json config.json
pip3 install markdown requests
```

Codex can use this repo in two ways:

1. Clone the repo into the current workspace and run the scripts directly.
2. Give Codex the repo URL or local path and ask it to call `scripts/format.py` or `scripts/publish.py`.

## Configuration

Edit `config.json`:

```json
{
  "output_dir": "./wechat-output-cache",
  "vault_root": "/path/to/your/obsidian/vault",
  "settings": {
    "default_theme": "newspaper",
    "auto_open_browser": true
  },
  "wechat": {
    "app_id": "YOUR_APP_ID",
    "app_secret": "YOUR_APP_SECRET",
    "author": "Author Name"
  },
  "cover": {
    "output_dir": "~/Documents/covers",
    "image_generation_script": ""
  }
}
```

- Normal formatting now writes to a new `wechat output/` folder next to the source Markdown file.
- `output_dir` is kept as a legacy fallback for older flows.
- `wechat` is only required for publishing.
- `cover` is only required for cover generation.

## Format an Article

### Open the Gallery

```bash
python3 scripts/format.py --input article.md --gallery
```

Default output:

```text
article.md
wechat output/
  gallery.html
  article.html
  preview.html
  images/
```

The browser page is only for preview and picking a style. After copying into WeChat, you can still fine-tune text manually in the editor.

### Format Directly

```bash
python3 scripts/format.py --input article.md --theme newspaper
```

### Adjustable Minimal Theme

```bash
python3 scripts/format.py \
  --input article.md \
  --theme minimal-flex \
  --accent blue \
  --heading-align center \
  --divider-style fade-short \
  --strong-style highlight
```

Available `minimal-flex` options:

- `--accent`: `black`, `gray`, `blue`, `green`, `red`, `navy`, `gold`
- `--heading-align`: `left`, `center`, `right`
- `--divider-style`: `solid-full`, `solid-short`, `soft-full`, `fade-short`, `bold-short`, `none`
- `--strong-style`: `color`, `highlight`

All heading levels from `#` to `#####` follow the alignment option.

## Style Selection Files

Gallery selections are written to the system temp directory:

- Windows example: `%TEMP%\wechat-format\selected-theme.txt`
- Windows example: `%TEMP%\wechat-format\selected-style.json`
- macOS / Linux example: `/tmp/wechat-format/selected-theme.txt`
- macOS / Linux example: `/tmp/wechat-format/selected-style.json`

`selected-style.json` is preferred. `selected-theme.txt` is kept for backward compatibility.

## Publish to WeChat Drafts

```bash
python3 scripts/publish.py --dir "/path/to/article-folder/wechat output" --cover cover.jpg
```

Or one-step input -> format -> publish:

```bash
python3 scripts/publish.py --input article.md
```

With `minimal-flex`:

```bash
python3 scripts/publish.py \
  --input article.md \
  --theme minimal-flex \
  --accent green \
  --heading-align right \
  --divider-style none \
  --strong-style color
```

## Windows Notes

- Do not rely on `/tmp/...` paths on Windows.
- `format.py --gallery` now prints both the real output folder and a local `http://127.0.0.1:.../gallery.html` address.
- Single-theme preview now prints an exact `file:///.../preview.html` URI.
- If the local gallery server cannot start, the script falls back to opening the generated `gallery.html` file directly.

## Theme Notes

- Existing theme IDs stay compatible.
- Gallery now shows a curated `极简 / minimal-flex` entry instead of listing several minimal child themes separately.
- Gallery uses subtle defaults instead of a separate "Recommended" badge to reduce visual noise.
- Older `minimal-*` themes are still available for direct CLI use.

## Container Syntax

```markdown
:::dialogue[Interview Title]
Alice: Hello there
Bob: Hi, how are you?
:::

:::gallery[Screenshots]
![](img1.jpg)
![](img2.jpg)
![](img3.jpg)
:::

> [!important] Key Insight
> This is the important takeaway
```

## Cover Workflow

```bash
python3 scripts/generate.py \
  --config cover/config.json \
  --prompt-file prompt.md \
  --out cover.jpg
```

See `cover/SKILL.md` for the cover workflow.

## Requirements

- Python 3
- `markdown`
- `requests` for publishing

## License

MIT
