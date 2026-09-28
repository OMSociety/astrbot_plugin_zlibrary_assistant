# Changelog

本项目的更改记录在此文件。

All notable changes to this project are documented in this file.

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.1] - 2026-09-25

### 安全

- **恢复 E-API 的 TLS 证书校验**：此前全部请求显式关闭校验，账号密码与长期有效的 remix 凭证在链路被劫持时会被直接读取。域名证书异常时会以网络错误暴露，不再静默降级。
- **远端返回的扩展名按白名单清洗**，并在落盘前再做一次目录包含性校验，杜绝以畸形扩展名写出下载目录。
- **封面解码前增加像素预算**：超预算或解码失败的封面一律丢弃，不再把原始字节回传内嵌；封面缺失时按占位降级。
- **核验真实建连的对端地址**（直连场景）：封住「校验时解析到公网、连接时解析到内网」的漂移。配置代理时该层不生效（域名由代理侧解析），代价已写入代码注释。
- **账号状态工具限定管理员会话**：账号邮箱与登录失败详情不再回到普通会话。
- **书目文本以第三方数据围栏标注**，并对所有远端字段压行限长，书名等字段不能再伪造新的指令行。
- 下载结果不再回显账号备注名。

### 修复

- 主 API 通道不再自动跟随重定向：改为**同源逐跳跟随**（最多 5 跳），跨主机或协议降级的跳转一律拒绝；响应体读取增加 8 MiB 上限。
- 下载目录按保留期清理过期文件。

### Security

- **Restored TLS certificate verification for the E-API**: every request previously turned verification off explicitly, so account passwords and long-lived remix credentials could be read directly by anyone who hijacked the connection. An invalid domain certificate now surfaces as a network error instead of a silent downgrade.
- **Remote-provided file extensions are sanitized against a whitelist**, with one more path-containment check before writing to disk, so a malformed extension can no longer be written outside the download directory.
- **Added a pixel budget before decoding covers**: a cover that exceeds the budget or fails to decode is discarded instead of having its raw bytes passed back for embedding; a missing cover falls back to the placeholder.
- **Verify the peer address of the actual connection** (direct-connection case): closes the drift where the host resolves to a public IP during validation but to an internal one when the connection is made. This layer is inactive when a proxy is configured (the domain is resolved by the proxy); the trade-off is recorded in a code comment.
- **The account status tool is restricted to admin sessions**: account emails and login-failure details no longer reach regular sessions.
- **Bibliographic text is fenced as third-party data**, and every remote field is collapsed onto a single line with a length cap, so a field such as a book title can no longer forge a new instruction line.
- Download results no longer echo the account note name.

### Fixed

- The main API channel no longer follows redirects automatically: it now **follows same-origin hops one at a time** (up to 5), and rejects any cross-host or protocol-downgrade redirect; reading a response body gained an 8 MiB cap.
- Expired files in the download directory are cleaned up after the retention period.

## [1.1.0] - 2026-09-13

### 安全

- **Tor/onion 访问（可选）**：onion 地址仅允许 v3 格式且必须经 SOCKS 访问；重定向仍逐跳执行 SSRF 校验。([#1](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/pull/1))
- 下载改为边读边限长，避免异常响应耗尽 AstrBot 内存。([#1](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/pull/1))

### 新增

- **WebUI 四语本地化**：新增 `zh-CN` / `en-US` / `ru-RU` / `ja-JP` 四份 i18n 文件（`.astrbot-plugin/i18n/`），插件名、简介与全部配置项文案在四种界面语言下均正确显示；中文文案与配置 schema 保持逐字镜像。受 AstrBot i18n 机制限制，账号模板中「账号备注名」字段的说明在非中文界面回退显示中文 schema 文案。
- 可选通过 Tor SOCKS 访问 v3 onion E-API，原有明网域名和 HTTP 代理配置保持兼容。([#1](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/pull/1))
- 提供可选 Tor Docker sidecar，支持使用现有 HTTP/SOCKS 服务作为 Tor 上游代理。([#1](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/pull/1))
- 新增可配置的单文件下载体积上限。([#1](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/pull/1))

### 修复

- 修正 `metadata.yaml` 中 `Astrbot` 的拼写为 `AstrBot`。

### 变更

- 账号池后台登录增加有限次重试，适配 Tor 容器冷启动。([#1](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/pull/1))

## [1.0.4] - 2026-09-07

### 安全

- **SSRF 纵深加固**：封面地址与下载直链均来自 Z-Library API 响应（第三方内容），此前仅校验协议前缀且默认跟随重定向，公网 302 可把请求带去内网。现新增 `zlib_security` 校验模块（参照 reverse_searcher 的 `utils/security.py`）：IP 黑名单（内网/环回/链路本地/云元数据/保留/多播全拒）+ DNS 解析二次校验（防 rebinding，含总开关）+ 重定向不自动跟随、手动逐跳校验（最多 3 跳）。`ssl=False` 为该站既有取舍，保留不动，由逐跳校验兜底。

### 变更

- 新增 SSRF 防护回归测试（39 用例）：IP 黑名单边界、DNS 二次校验、重定向逐跳拒绝与跳数上限、封面/下载入口拒绝。

## [1.0.3] - 2026-09-07

### 修复

- 下载额度用尽的提示不再硬编码"每日 10 次"，按账号实际额度（API `downloads_limit`）显示。
- 移除账号池配置的 dict 兼容分支（经提交历史核实从未存在过该结构，配置始终为 template_list）。

### 变更

- 封面下载改为边读边限长，异常大图不再整份读入内存。
- 表单 Content-Type 请求头仅在带请求体的 POST 上发送（GET 不再携带）。

## [1.0.2] - 2026-09-06

### 修复

- **修复书籍缓存被 base64 封面污染**：搜索结果缓存的是原 dict 引用，封面下载后写入的巨型 base64 字符串会随缓存持久化到磁盘，`book_cache.json` 无限膨胀。现缓存浅拷贝，下载路径只读 `id`/`hash`，行为不变。
- 修复错误分类过宽：所有 `Err #N` 开头的 E-API 业务错误（文件不存在、参数错误等）此前都被误报为"IP 被限流"，现只匹配明确的限流文案。

### 变更

- 书籍缓存增加 500 条上限（按写入顺序淘汰最旧条目），避免长期运行无限膨胀。
- 移除与框架默认值相同的渲染 options 等冗余配置（行为不变）。

## [1.0.1] - 2026-09-03

### 安全

- **修复搜索结果卡片的 HTML 注入风险**：书籍标题/作者等元信息是 Z-Library 返回的第三方内容，搜索关键词是用户输入，此前原样进 Jinja2 模板交给云端 t2i 渲染，而云端渲染器是否自动转义不受控。现于渲染前用 `html.escape` 对全部插值字段（`query` / `title` / `author` / `extension` / `language` / `year` / `filesizeString` / `cover`）逐字段转义（与 reverse_searcher 1.0.3 同源同修）

### 新增

- 新增最小测试集（12 用例）：渲染数据转义、mojibake 双重编码修复；本插件首次建立回归防线

### 变更

- 修复 4 处 import 排序（ruff I001），`ruff check` / `ruff format` 全过
