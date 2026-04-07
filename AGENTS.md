# AGENTS.md

## Purpose

This repo is a script-first WeChat formatting toolkit. Claude and Codex can both use it, and the same scripts also work from the CLI.

## Main Entry Points

- `scripts/format.py`: convert Markdown into WeChat-compatible HTML
- `scripts/publish.py`: publish formatted output to WeChat drafts
- `templates/gallery.html`: browser gallery picker
- `templates/preview.html`: single-theme preview shell
- `themes/*.json`: theme definitions

## Agent Workflow

1. Read `README.md` or `README_CN.md`.
2. If the user gives you a Markdown path, format that file directly.
3. By default, write output to a new `wechat output/` folder next to the source Markdown file.
4. Use `python scripts/format.py --input article.md --gallery` when the user wants to pick a theme visually.
5. Reuse the last gallery selection from the system temp directory:
   `selected-style.json` first, `selected-theme.txt` second.
6. Tell the user that the gallery is a preview and picker. After copying to WeChat, they can still fine-tune text manually.
7. When gallery rendering finishes, give the user both addresses:
   the local `http://127.0.0.1:.../gallery.html` URL and the generated local file URI such as `file:///D:/.../wechat%20output/gallery.html`.
8. Prefer proactively testing the local `127.0.0.1` gallery URL before reporting success. Even when that URL works, still remind the user that the local file in `wechat output/` also exists and is usable.

## Theme Rules

- Existing theme IDs are compatibility-sensitive.
- The adjustable minimal theme is `minimal-flex`.
- Variant controls belong only to `minimal-flex`.
- In the gallery, the `极简` group should point users to `minimal-flex` instead of listing several minimal child themes separately.

## Output Rules

- Do not create extra nested output folders unless the user explicitly asks for that structure.
- The normal result should stay inside the Markdown file's sibling `wechat output/` folder.
- On Windows, prefer reporting the real local file path or the local `http://127.0.0.1:...` gallery URL. Do not hand back `/tmp/...` paths as if they were directly openable.
- Good UX matters here: report the tested URL, report the file URI, and make clear that both are valid ways to open the result.

## Publishing Rules

- Do not change WeChat API behavior unless the task explicitly requires it.
- Keep backward compatibility with `selected-theme.txt` while preferring `selected-style.json`.
