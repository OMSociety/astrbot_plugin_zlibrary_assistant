# Changelog

本项目所有重要更改都会记录在此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.1] - 2026-09-03

### 🔒 安全

- **修复搜索结果卡片的 HTML 注入风险**：书籍标题/作者等元信息是 Z-Library 返回的第三方内容，搜索关键词是用户输入，此前原样进 Jinja2 模板交给云端 t2i 渲染，而云端渲染器是否自动转义不受控。现于渲染前用 `html.escape` 对全部插值字段（`query` / `title` / `author` / `extension` / `language` / `year` / `filesizeString` / `cover`）逐字段转义（与 reverse_searcher 1.0.3 同源同修）

### ✨ 新增

- 新增最小测试集（12 用例）：渲染数据转义、mojibake 双重编码修复；本插件首次建立回归防线

### ⚙️ 变更

- 修复 4 处 import 排序（ruff I001），`ruff check` / `ruff format` 全过
