<p align="center"><strong>中文</strong> · <a href="README_en.md">English</a> · <a href="README_ru.md">Русский</a> · <a href="README_ja.md">日本語</a></p>

<div align="center">

<h1><img src="https://raw.githubusercontent.com/OMSociety/astrbot_plugin_zlibrary_assistant/main/logo.png" width="192" alt="Zlibrary 助手 Zlibrary Assistant"><br>Zlibrary 助手 Zlibrary Assistant</h1>

**Z-Library 图书搜索与下载助手** —— 多镜像自动故障转移 · `/zlib` 指令 · 卡片图片结果 · 账号池额度管控

[![Version](https://img.shields.io/github/v/tag/OMSociety/astrbot_plugin_zlibrary_assistant?label=version&color=blue)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/releases)
[![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A5v4-green.svg)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/OMSociety/astrbot_plugin_zlibrary_assistant)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/stargazers)
[![Issues](https://img.shields.io/github/issues/OMSociety/astrbot_plugin_zlibrary_assistant)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/issues)

[快速开始](#快速开始) • [指令](#指令) • [LLM 工具](#llm-可调用工具) • [镜像域名](#镜像域名与故障转移) • [配置项](#配置项说明) • [Tor/onion](#toronion-部署) • [架构](#架构) • [常见问题](#常见问题) • [更新日志](CHANGELOG.md)

</div>

> 本项目由 AI 编写

Zlibrary 助手为 AstrBot 提供 Z-Library 图书搜索与下载：内置多个镜像域名并自动故障转移，既可用 `/zlib` 指令也可由 LLM 工具调用；搜索结果以卡片图片展示，下载经账号池按剩余额度轮换。

---

## 快速开始

在 AstrBot WebUI 的插件市场搜索「Zlibrary助手」并安装。

### 最小配置

1. 只搜索：无需任何配置（服务器直连不通镜像时，在 `proxy` 填代理）。
2. 要下载：在 `accounts` 添加一个账号，推荐填 `remix_userid` + `remix_userkey`。浏览器登录 Z-Library 后按 F12，在 应用(Application) → Cookie 中复制这两项的值；它们长期有效，且不经过易被风控的登录端点。也可填 `email` + `password`。
3. 保存并重载插件，对机器人发送：

```
/zlib 搜索 资本论 -l zh
/zlib 下载 1
```

> **提示**：官方域名启用了浏览器验证，部分出口 IP 无法通过，此时插件自动转到内置镜像；`/zlib 诊断` 可查看各镜像当前是否可达。

---

## 指令

指令组 `/zlib`，别名 `/zl`。

| 指令 | 说明 |
|------|------|
| `/zlib 搜索 关键词 [-l 语言] [-e 格式] [-n 条数]` | 搜索；例如 `/zlib 搜索 资本论 -l zh -e pdf`。别名 `搜`、`search`、`s` |
| `/zlib 下一页` / `/zlib 上一页` | 对最近一次搜索翻页。别名 `next`/`n`、`prev`/`p` |
| `/zlib 下载 序号或id` | 序号取自当前会话最近一次结果；超出本页条数的数字按书籍 id 处理。别名 `下`、`download`、`dl` |
| `/zlib 状态` | 账号登录状态与今日额度（仅管理员）。别名 `status` |
| `/zlib 诊断` | 探测每个镜像的可达性与延迟、检查账号登录（仅管理员）。别名 `diag` |
| `/zlib 帮助` | 指令说明。别名 `help` |

语言参数接受 `zh`、`en`、`chinese`、`english` 等写法。搜索不消耗下载额度；`download_admin_only` 开启后下载仅管理员可用。

---

## LLM 可调用工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `zlib_search_books` | `query`（必填）、`limit`、`page`、`language`、`extension` | 搜索，返回卡片图片路径与带 id 的文本书目 |
| `zlib_download_book` | `book_id`（必填） | 按搜索结果 id 下载，消耗额度，仅在用户明确要求时调用 |
| `zlib_get_status` | 无 | 账号池状态，仅管理员会话可用 |

```
用户: 帮我找一本马克思的《资本论》，中文 PDF
模型调用: zlib_search_books(query="资本论", language="zh", extension="pdf")
用户: 下载第 1 本
模型调用: zlib_download_book(book_id=<搜索结果中的 id>)
```

书目字段进入工具结果前会清除围栏标记并截断，降低书名中夹带指令造成提示注入的风险。工具返回文件路径后，由模型调用 `send_message_to_user`（`type=file`）发送。

---

## 镜像域名与故障转移

- 内置域名按顺序尝试：官方入口 `z-library.sk`、`z-lib.sk`、`z-library.im`、`z-library.hn` 优先；其后是镜像 `z-lib.by`、`z-library.website`、`z-lib.gd`、`z-lib.gl`、`article.sk`、`articles.sk`、`z-library.qa`、`zlib.bz`。
- 请求带 `source: android` 头访问镜像的 E-API；网络错误、证书错误、浏览器验证拦截、Cloudflare 拦截、限流都会触发故障转移，并让该镜像按错误类型冷却一段时间。
- `domains` 中填写的域名优先级最高；其后是内置镜像；从镜像学到的域名排在最后，且只用于不带账号凭证的搜索请求。
- 镜像能否访问取决于你的出口网络，请以 `/zlib 诊断` 的结果为准。

---

## 配置项说明

### 账号设置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `accounts` | template_list | `[]` | 账号池，每项一个账号；remix 与邮箱密码同时填写时以 remix 为准 |
| `accounts[].name` | string | `account` | 账号备注名，用于日志与状态显示 |
| `accounts[].remix_userid` | string | `""` | remix_userid（推荐，与 remix_userkey 成对） |
| `accounts[].remix_userkey` | string | `""` | remix_userkey（推荐） |
| `accounts[].email` | string | `""` | 账号邮箱（备选，与密码成对） |
| `accounts[].password` | string | `""` | 账号密码（备选） |

### 连接设置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `domains` | list | `[]` | 额外镜像域名，只填主机名；onion 地址需要 SOCKS 代理 |
| `proxy` | string | `""` | HTTP 或 SOCKS 代理，如 `http://127.0.0.1:7890` |
| `max_download_mb` | int | `80` | 单个下载文件体积上限（MiB，1-512） |

### 搜索与下载设置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `search_limit` | int | `5` | 搜索默认条数（1-10） |
| `card_enabled` | bool | `true` | 以卡片图片展示搜索结果；关闭或渲染失败时回复文本书目 |
| `download_admin_only` | bool | `false` | 下载仅管理员可用 |

### 快速配置模板

`data/config/astrbot_plugin_zlibrary_assistant_config.json`：

```json
{
  "accounts": [
    {"name": "account1", "remix_userid": "", "remix_userkey": "", "email": "", "password": ""}
  ],
  "domains": [],
  "proxy": "",
  "max_download_mb": 80,
  "search_limit": 5,
  "card_enabled": true,
  "download_admin_only": false
}
```

---

## Tor/onion 部署

插件可通过 Tor 访问 onion 入口：`.onion` 域名必须配合 SOCKS 代理，DNS 解析在 Tor 端完成。插件不提供任意 URL 浏览能力，避免机器人被提示注入后充当开放代理。

### Docker 部署

1. 把下面的 `tor` 服务合并到 AstrBot 的 `docker-compose.yml`（不要把 9050 映射到公网）：

```yaml
services:
  tor:
    build: ./data/plugins/astrbot_plugin_zlibrary_assistant/docker/tor
    restart: unless-stopped
    # Tor 需经现有代理出网时再启用：
    # environment:
    #   - TOR_UPSTREAM_PROXY=http://proxy-host:port
```

2. 确保 AstrBot 与 `tor` 在同一 Compose 网络，然后 `docker compose up -d --build tor astrbot`。
3. 在插件配置中填写（onion 地址请自行确认当前可用）：

```json
{
  "domains": ["http://loginzlib2vrak5zzpcocc3ouizykn6k5qecgj2tzlnab5wcbqhembyd.onion"],
  "proxy": "socks5://tor:9050"
}
```

普通 HTTP/SOCKS 代理不是 onion 路由器，不能代替 Tor。

### 非 Docker 部署

安装并启动 Tor，使 SOCKS 端口只监听本机，将 `proxy` 设为 `socks5://127.0.0.1:9050`。AstrBot 在 Docker、Tor 在宿主机时，Docker Desktop 通常用 `socks5://host.docker.internal:9050`，并需让 Tor 监听 Docker 可达的接口、以防火墙限制来源。

### 安全约束

- 只接受 HTTP/HTTPS；`.onion` 必须是 56 字符的 v3 地址，且必须配置 SOCKS 代理。
- API 与下载的每一跳重定向都重新做 SSRF 校验。
- Tor 服务不发布宿主机端口，仅供 Compose 内部网络使用。

---

## 架构

| 模块 | 职责 |
|------|------|
| `zlib_transport.py` | aiohttp 传输层：请求头、手动同源重定向与 Cookie 合并、响应分类、有界读取、受保护的下载 GET（SSRF 与对端核验） |
| `zlib_domains.py` | 镜像池：内置/自定义/学习三级优先、冷却、故障转移、批量探测 |
| `zlib_client.py` | E-API 客户端：账号池登录、搜索、下载链接、封面抓取、状态与诊断 |
| `zlib_service.py` | 指令与工具共用的业务层：搜索、卡片、序号解析、下载落盘 |
| `zlib_cards.py` | 卡片 HTML 模板与渲染数据（所有书目字段显式转义） |
| `zlib_store.py` | 书目缓存（持久化）与按会话的搜索记忆 |
| `zlib_format.py` | 指令参数解析与文本格式化 |
| `tools/` | 三个 LLM 工具 |

下载文件保存在 `<data>/plugin_data/zlibrary_assistant/books/`，保留 7 天。

---

## 常见问题

### Q1：所有镜像都不可用，或提示「DiamWall 验证拦截」？

先执行 `/zlib 诊断`。官方主域启用了浏览器验证，脚本无法通过；镜像是否可达取决于出口 IP，换代理节点往往有效。也可在 `domains` 添加你自己验证过可用的镜像。

### Q2：提示「IP 被限流」？

站点对访问频率与共享出口 IP 有风控。更换代理节点或降低请求频率；该镜像会自动冷却，期间转移到其他镜像。

### Q3：下载提示「账号池今日下载额度已全部用完」或「未配置任何账号」？

免费账号每日下载次数有限（以账号状态为准）。等待次日重置，或在 `accounts` 添加更多账号。

### Q4：邮箱密码登录失败？

登录端点经常被风控单独拒绝。改填 `remix_userid` + `remix_userkey`（见[最小配置](#最小配置)）。

### Q5：搜索结果图片里的中文变成方块？

AstrBot 文转图优先走云端渲染，失败时回退本地渲染，本地渲染需要系统中文字体，而官方镜像未预装。解决（任选其一）：

1. Dockerfile 加一行：`RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk`
2. 运行中的容器：`docker exec -it <容器名> bash -c "apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk"`
3. 把任意中文字体复制为 `data/font.ttf`：`docker cp msyh.ttc <容器名>:/AstrBot/data/font.ttf`

### Q6：LLM 下载完成但发不出文件，提示 "Sandbox runtime is disabled by configuration"？

`send_message_to_user` 发送本地文件依赖 AstrBot 的 `computer_use_runtime`，默认为 `none`。在 WebUI 的 配置 → 服务提供商设置 把 `computer_use_runtime` 改为 `local`（必要时把 `computer_use_require_admin` 设为 `false`），或在 `data/config/astrbot_config.json` 写入：

```json
{"provider_settings": {"computer_use_runtime": "local", "computer_use_require_admin": false}}
```

保存后重启 AstrBot。`/zlib 下载` 指令直接发送文件，不受此项影响。

### Q7：Docker 部署时搜索很慢、封面全是占位？

容器是独立网络环境，`http://127.0.0.1:端口` 指向容器自己。`proxy` 请填容器可访问的地址：Docker Desktop 可用 `http://host.docker.internal:端口`，Linux 用宿主机局域网 IP 并在代理软件中允许局域网连接；需要认证时写 `http://用户名:密码@IP:端口`。云端文转图不可达时，渲染有 25 秒超时保护并自动降级为文本书目；仍嫌紧可调大 `agent_runner.config.misc.tool_call_timeout`。

## 更新日志

> **[查看完整更新日志 →](CHANGELOG.md)**

## 支持与致谢

如果这个插件对你有帮助，欢迎点亮 Star，有问题和建议请提交 [Issue](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/issues) 或 [Pull Request](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/pulls)。

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) 开源聊天机器人框架

## 许可证与作者

本项目采用 **MIT License** 开源协议。

[@OMSociety](https://github.com/OMSociety)
