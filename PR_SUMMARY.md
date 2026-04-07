# PR Summary

## English

### Summary

This round improves the formatting workflow for both Claude and Codex, upgrades the gallery UX, adds a configurable minimal theme, and makes Windows output paths and preview links much more reliable.

### What Changed

#### 1. Output behavior

- Changed the default output location to a new `wechat output/` folder next to the source Markdown file.
- Removed the need for extra nested output folders in the normal formatting flow.
- Kept backward-compatible fallback logic in `publish.py` for older output layouts.

#### 2. Gallery UX

- Removed tool-specific wording and updated the UI to clearly support both Claude and Codex.
- Grouped major theme categories on separate rows instead of mixing category labels with buttons.
- Added a clear note that users can still fine-tune text after pasting into WeChat.
- Made the copy button carry the current style directly, so users do not need to click a separate confirm button first.
- Removed the separate "Recommended" badge and switched to subtler default emphasis.

#### 3. Adjustable minimal theme

- Added `minimal-flex` as the single adjustable minimal entry in the gallery.
- Added controls for:
  - `accent`
  - `heading_align`
  - `divider_style`
  - `strong_style`
- Applied heading alignment to heading levels `#` through `#####`.
- Linked heading color and bold text color to the same selected accent.
- Expanded divider options and added a highlight-style bold treatment.

#### 4. Style persistence

- Added structured style persistence via `selected-style.json`.
- Kept `selected-theme.txt` for backward compatibility.
- Updated formatting and publishing flows to prefer the structured selection when available.

#### 5. Docs and agent guidance

- Updated `README.md` and `README_CN.md` to state that Claude and Codex can both use the repo.
- Documented the default `wechat output/` behavior.
- Added agent-facing guidance in `AGENTS.md` and updated `SKILL.md` to keep agents on the same script-first workflow.

#### 6. Windows reliability

- Improved local gallery startup checks.
- Print real local output paths and usable preview URLs.
- Fall back to opening the generated `gallery.html` file directly if the local HTTP gallery server fails to start.
- Avoid misleading `/tmp/...` output guidance on Windows.

### Validation

- Ran `python tests/test_format_smoke.py`
- Verified direct formatting with `minimal-flex`
- Verified gallery generation for real articles
- Verified that Windows local gallery URLs respond with HTTP `200`
- Verified that default output is created beside the source Markdown file as `wechat output/`

## 中文

### 概要

这一轮主要做了四件事：统一 Claude / Codex 工作流、重做 gallery 交互、加入可调极简主题，以及把 Windows 下的输出路径和预览链接稳定性补上。

### 改动内容

#### 1. 输出行为

- 默认输出目录改成源 Markdown 同级的 `wechat output/`
- 正常排版流程不再额外套娃生成多层输出目录
- `publish.py` 里保留了对旧输出结构的兼容兜底

#### 2. Gallery 交互

- 去掉只绑定单一工具的文案，明确 Claude 和 Codex 都能用
- 把主干主题分类拆成独立行显示，不再把分类名和按钮混排
- 增加提醒：复制到公众号后台后，用户仍然可以继续微调文字
- “复制内容”按钮现在会直接带走当前样式，不需要先点确认
- 去掉单独的“推荐”角标，改成更克制的默认强调

#### 3. 可调极简主题

- 新增 `minimal-flex`，作为 gallery 里唯一的极简入口
- 增加 4 个可调维度：
  - `accent`
  - `heading_align`
  - `divider_style`
  - `strong_style`
- 标题对齐从 `#` 一直覆盖到 `#####`
- 标题颜色和加粗文字颜色共用同一强调色
- 分隔线扩展为更多轻重样式，并补了荧光笔式加粗强调

#### 4. 样式选择持久化

- 新增 `selected-style.json` 结构化保存样式结果
- 继续保留 `selected-theme.txt` 兼容旧流程
- `format.py` 和 `publish.py` 都优先读取结构化结果

#### 5. 文档和 Agent 说明

- 更新 `README.md` 和 `README_CN.md`，明确 Claude、Codex 都可用
- 文档里写清默认输出到同级 `wechat output/`
- 新增 `AGENTS.md`，并更新 `SKILL.md`，让不同 Agent 都沿用同一套脚本工作流

#### 6. Windows 稳定性

- 补了本地 gallery 服务启动检查
- 输出真实可用的本地目录和预览 URL
- 如果本地 HTTP gallery 服务起不来，会自动回退到直接打开生成的 `gallery.html`
- 避免在 Windows 上继续误报 `/tmp/...` 这类不可直接打开的路径

### 验证

- 运行了 `python tests/test_format_smoke.py`
- 验证了 `minimal-flex` 的直接排版
- 验证了真实文章的 gallery 生成
- 验证了 Windows 下本地 gallery URL 返回 HTTP `200`
- 验证了默认输出确实落在源 Markdown 同级 `wechat output/`
