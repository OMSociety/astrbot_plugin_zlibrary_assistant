# Changelog

本项目的更改记录在此文件。

All notable changes to this project are documented in this file.

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.1] - 2026-11-27

### 新增

- **配置项 `search_timeout`**：搜索、翻页与额度查询的超时（秒），默认 30，范围 5-300；LLM 工具与 `/zlib` 指令共用。
- **镜像共享探测**：域名池没有 300 秒内成功过的镜像时，第一次搜索前并发探测全部镜像（单个最多 10 秒），结果写入域名池，所有会话共用；并发搜索共用同一次探测，镜像全部不可达时两次探测至少间隔 60 秒，后台预热也先探测一次。
- **下载在后台进行**：`zlib_download_book` 提交后立即返回，模型的回合随之结束，下载期间同一会话仍可正常对话；文件下载完成后由插件直接发给用户，失败时也由插件直接说明原因，不再唤醒模型。同一本书重复提交只下一次。
- **下载按空闲判停**：连续 30 秒没有收到数据才判定中断，只要数据仍在到达，大文件慢速下载不会被总时限掐断；取下载链接限时 30 秒，下载后刷新额度限时 10 秒。
- **下载并发上限 2**：同时进行的下载最多两个，其余排队；进行中的下载先占住所用账号的额度，并发下载不会重复占用同一份额度。

### 变更

- **搜索、翻页与额度查询的默认超时由 20 秒放宽到 30 秒**：每个不可达的镜像要耗掉约 8 秒连接超时，20 秒只够换两个域名，慢网络下搜索常在拿到结果前超时，模型随即反复重搜。
- 超时提示按实际配置值显示秒数，指令侧的提示同时指出可调大 `search_timeout`。
- **下载不再占用对话**：下载工具由同步等待改为提交即返回，成功与失败都由插件主动发给用户；额度只有全部用完时才作为一次提示交给模型，其余时候不再出现在工具结果里。

### 移除

- **`/zlib 3` 下载简写**：下载统一用 `/zlib 下载 3`（或 `/zlib dl 3`）。裸数字不再被插件截获。
- **内置镜像 `z-lib.by`**：该域名已不可用，从内置域名列表移除；`domains` 配置项里的范例域名改用 `z-lib.sk`。

### Added

- **`search_timeout` option**: timeout in seconds for search, paging and quota checks; default 30, range 5-300; shared by the LLM tools and the `/zlib` commands.
- **Shared mirror probe**: when no mirror has succeeded within the last 300 seconds, the first search probes every mirror concurrently (at most 10 seconds each) and writes the result into the domain pool, shared by all sessions; concurrent searches share one probe, and when no mirror is reachable probes are at least 60 seconds apart; background warm-up probes first as well.
- **Downloads run in the background**: `zlib_download_book` returns as soon as the download is submitted, so the model's turn ends and the same session stays usable while the file is transferring; the plugin sends the finished file to the user, and on failure states the reason itself instead of waking the model. Submitting the same book twice downloads it once.
- **Downloads stop on idleness, not on a fixed total**: transfer is abandoned only after 30 seconds without receiving data, so a large slow download keeps going as long as bytes are arriving; the download link request is limited to 30 seconds and the post-download quota refresh to 10 seconds.
- **At most two downloads run at once**: the rest queue up, and an in-flight download reserves the quota of the account it uses so concurrent downloads cannot both spend the same remaining download.

### Changed

- **The default timeout for search, paging and quota checks is raised from 20 to 30 seconds**: every unreachable mirror burns about 8 seconds on its connect timeout, so 20 seconds only covers two domains; on a slow network searches often time out before any result arrives and the model then searches again and again.
- The timeout message shows the configured number of seconds, and the command-side message points out that `search_timeout` can be raised.
- **Downloads no longer block the conversation**: the download tool returns immediately instead of waiting, and both the finished file and any failure are sent to the user by the plugin; remaining quota reaches the model only once, when the pool is used up.

### Removed

- **The `/zlib 3` download shorthand**: use `/zlib download 3` (or `/zlib dl 3`) for downloads. A bare number is no longer intercepted by the plugin.
- **The built-in mirror `z-lib.by`**: the domain is no longer reachable and is dropped from the built-in list; the example in the `domains` option now uses `z-lib.sk`.

## [1.2.0] - 2026-11-27

### 新增

- **`/zlib` 指令组**（别名 `/zl`）：`搜索`（支持 `-l` 语言、`-e` 格式、`-n` 条数）、`详情`、`下一页`、`上一页`、`下载`（按序号或书籍 id）、`状态`、`诊断`、`帮助`；序号取自当前会话最近一次搜索结果。`/zlib 3` 是 `/zlib 下载 3` 的简写。
- **`/zlib 详情`**：查看一本书的出版社、页数、丛书、版次、分类与简介。
- **镜像域名池与自动故障转移**：内置官方入口（`z-library.sk`、`z-lib.sk`、`z-library.im`、`z-library.hn`）优先、其后一组可直连镜像；网络错误、证书错误、浏览器验证拦截、Cloudflare 拦截、限流都会触发换镜像，并按错误类型冷却。可从镜像的 `/eapi/info/domains` 学习新域名（至多 8 个，仅承接匿名请求，带账号凭证的请求只发往内置与用户配置的镜像）。
- **`/zlib 诊断`**：探测每个镜像的可达性与延迟，并检查账号登录。
- **配置项**：`domains`（额外镜像）、`card_enabled`（卡片图片开关）、`download_admin_only`（下载仅管理员）。
- **`zlib_search_books` 新增 `page` 参数**，搜索结果按会话记忆，支持翻页与按序号下载。
- 响应分类新增 `diamwall_blocked`、`cloudflare_blocked`、`tls_error`、`all_domains_failed`，错误提示给出对应处理方向。

### 变更

- **请求固定携带 `source: android` 头**，使镜像的 E-API 接受搜索请求。
- **搜索无需账号**；下载才需要账号。登录改为惰性触发（带锁与冷却），插件加载时后台预热一次。
- **账号凭证以 remix 为主**；下载按剩余额度循环账号，登录失效或额度耗尽自动换号，下载后刷新额度。
- **搜索结果卡片重新设计**：构成主义配色（黑、红、米白），黑色抬头配关键词与页码、序号红黑交替的条列、按格式配色的标签、页脚操作提示；JPEG 质量由默认的 40 提升到 90。所有书目字段在模板中显式转义（渲染端不启用 Jinja2 autoescape）。
- **工具交付内聚**：结果卡片与下载好的文件由插件直接发送，模型不再需要转述本地路径，也不依赖 `send_message_to_user` 的文件白名单与 `computer_use_runtime` 设置。
- **搜索、翻页与额度查询限时 20 秒**：镜像不稳时逐个试域名会把一轮对话拖上几分钟，超时即回一句提示让人重试；卡片渲染单独限时 15 秒，超时按纯文本回退。
- **搜索结果带简介进入模型上下文**：工具结果的书目行后附清洗过的简介（去 HTML、压行、限长），模型据此判断版本，不必反复搜索；下载进度改为流式写盘，完成后一次性汇报耗时与剩余额度。

### 移除

- 配置项 `domain` 由 `domains` 取代；默认域名改为内置的官方入口与镜像列表。升级后请重新核对配置。
- 账号项的备注名字段由 `name` 改名为 `label`：该项原与模板名共用同一个 i18n 键，导致备注名无法随语言本地化。升级后账号需要重新填写备注名，remix 与邮箱密码字段不受影响。

### Added

- **`/zlib` command group** (alias `/zl`): `search` (with `-l` language, `-e` extension, `-n` count), `detail`, `next`, `prev`, `download` (by index or book id), `status`, `diag`, `help`; indexes refer to the latest search results of the current session. `/zlib 3` is shorthand for `/zlib download 3`.
- **`/zlib detail`**: publisher, pages, series, edition, categories and description of one book.
- **Mirror pool with automatic failover**: the built-in official entries (`z-library.sk`, `z-lib.sk`, `z-library.im`, `z-library.hn`) come first, followed by a set of directly reachable mirrors; network errors, certificate errors, browser-verification blocks, Cloudflare blocks and rate limiting all trigger a switch and a per-error-type cooldown. New domains can be learned from a mirror's `/eapi/info/domains` (at most 8, anonymous requests only; requests carrying account credentials go only to built-in and user-configured mirrors).
- **`/zlib diag`**: probes every mirror for reachability and latency and checks account logins.
- **Options**: `domains` (extra mirrors), `card_enabled` (card image switch), `download_admin_only` (admin-only downloads).
- **`zlib_search_books` gains a `page` parameter**; search results are remembered per session for paging and index-based downloads.
- New response categories `diamwall_blocked`, `cloudflare_blocked`, `tls_error`, `all_domains_failed`, each with an actionable message.

### Changed

- **Requests always carry the `source: android` header**, which the mirrors' E-API requires for search.
- **Searching needs no account**; only downloading does. Login is lazy (locked, with a cooldown) and warmed up once in the background when the plugin loads.
- **Account credentials prefer remix**; downloads loop over accounts by remaining quota, switching on login failure or exhausted quota, and refresh the quota afterwards.
- **Search result card redesigned**: constructivist palette (black, red, cream), a black header carrying the query and page count, alternating red and black index columns, format-colored tags and a footer hint; JPEG quality raised from the default 40 to 90. Every book field is explicitly escaped in the template (the renderer does not enable Jinja2 autoescape).
- **Tool delivery is self-contained**: the plugin sends the result card and the downloaded file itself, so the model no longer relays local paths and nothing depends on `send_message_to_user`'s file allow-list or on `computer_use_runtime`.
- **Search, paging and quota checks are capped at 20 seconds**: when mirrors are flaky, trying them one by one used to drag a whole turn out for minutes; the cap replies with a hint to retry. Card rendering has its own 15-second cap and falls back to plain text.
- **Descriptions reach the model with the search results**: each book line in a tool result is followed by a cleaned description (HTML stripped, single line, length-capped) so the model can tell editions apart instead of searching again; downloads stream to disk and report elapsed time and remaining quota once at the end.

### Removed

- The `domain` option is replaced by `domains`; the default domains are now the built-in official entries and mirror list. Re-check your configuration after upgrading.
- The account label field is renamed from `name` to `label`: it shared an i18n key with the template name, so the label could not be localized. Re-enter the account labels after upgrading; remix and email/password fields are unaffected.

## [1.1.2] - 2026-11-16

### 修复

- 书籍缓存与下载文件存入 `data/plugin_data/zlibrary_assistant/`，遵循 AstrBot 插件数据目录规范。

### Fixed

- The book cache and downloaded files are stored under `data/plugin_data/zlibrary_assistant/`, following AstrBot's plugin data directory convention.

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
