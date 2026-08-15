<div align="center">

<img src="https://raw.githubusercontent.com/OMSociety/astrbot_plugin_zlibrary_assistant/main/logo.png" width="120" alt="ZLibrary Assistant Logo" />

# 📚 Zlibrary 助手

**Z-Library 图书搜索与下载助手** —— 图书搜索 · 一键下载 · 账号池轮换 · HTML 卡片结果 · 额度管控

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant)
[![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A5v4-green.svg)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/OMSociety/astrbot_plugin_zlibrary_assistant)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/stargazers)
[![Issues](https://img.shields.io/github/issues/OMSociety/astrbot_plugin_zlibrary_assistant)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/issues)

[✨ 核心特性](#-核心特性) • [📖 功能概览](#-功能概览) • [🚀 快速开始](#-快速开始) • [⚙️ 配置项说明](#️-配置项说明) • [🛠️ LLM 可调用工具](#️-llm-可调用工具) • [🧩 架构](#-架构) • [🔧 常见问题](#-常见问题) • [📝 更新日志](CHANGELOG.md)

</div>

> 🎨 本项目由 AI 编写

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🔍 **图书搜索** | 关键词搜索 Z-Library（3300 万+ 本书），返回 HTML 卡片图片（封面/标题/作者/格式/大小）+ 带 id 的文本列表 |
| 📥 **一键下载** | 说一声就下载，文件自动发送到会话，支持 pdf / epub / mobi 等格式 |
| 👥 **账号池轮换** | 多账号共享每日下载额度，自动挑选剩余额度最多的账号，耗尽拒绝并友好提示 |
| 🔑 **双凭据方式** | `remix_userid+remix_userkey`（绕开 login 端点风控）或 `email+password`（自动换取 remix 凭证） |
| 🎴 **HTML 卡片结果** | 搜索结果渲染为统一卡片图片，封面加载失败自动降级渐变占位，卡片不空白 |
| 🛡️ **错误分类** | IP 限流 / 域名失效 / 登录失效 / 额度耗尽 / 网络异常全部分类处理，友好中文提示，插件不崩溃 |

---

## 📖 功能概览

### 图书搜索
聊天中直接说想找什么书，LLM 自动调用搜索工具，返回 **HTML 卡片图片**（封面/标题/作者/格式/大小）和带 id 的文本列表：

```
用户: 帮我找一本马克思的《资本论》
🤖 → zlib_search_books(query=资本论)
    搜索「资本论」命中 N 本，卡片图已发送 ✅
```

### 图书下载
说一声就能下载，文件自动发送到会话：

```
用户: 下载第 1 本
🤖 → zlib_download_book(book_id=搜索结果中的id)
    下载完成 ✅ 已发送 PDF 文件，账号剩余额度 9 次
```

### 账号池与额度管控
- 配置多个 Z-Library 账号，**每个账号独立每日下载额度**（免费约 10 次/天）
- 下载时自动轮换到**今日剩余额度最多**的账号，全部耗尽则拒绝并提示
- 支持两种凭据方式：`email+password` 或 `remix_userid+remix_userkey`

### HTML 卡片结果
搜索结果渲染为统一卡片图片（Z-Library 封面 + 元信息徽章），封面加载失败自动降级为渐变占位，卡片不空白。

### 错误分类
IP 限流 / 域名失效 / 登录失效 / 额度耗尽 / 网络异常 全部分类处理，翻译为友好中文提示，插件不因单次错误崩溃。

---

## 🚀 快速开始

### 第一步：安装

**方式一：插件市场**
- AstrBot WebUI → 插件市场 → 通过 GitHub 安装 `astrbot_plugin_zlibrary_assistant`

**方式二：手动安装**
1. 将插件文件夹放入 `/AstrBot/data/plugins/`
2. 重启 AstrBot
3. 在管理面板按需配置各项参数

### 第二步：最小配置（跑通搜索与下载）

只需在 WebUI 插件配置页的 `accounts` 里添加一个账号（推荐 remix 方式）：

1. 打开插件配置页 → `accounts` 点击添加账号条目
2. **推荐方式**：填 `remix_userid` + `remix_userkey`（浏览器登录 z-library.sk 后按 F12 → Cookie 里复制，长期有效，不受 login 端点风控影响）
3. 或填 `email` + `password`（登录后自动换取 remix 凭证）
4. 如需多个账号，继续添加条目（账号池自动轮换）

保存后重启 AstrBot，即可在对话中直接搜书、下载。

> 💡 国内服务器需在 `proxy` 填代理（如 `http://127.0.0.1:7897`），境外服务器可留空。

### 依赖安装
插件仅依赖 `aiohttp` + `aiofiles`（异步 HTTP/文件库），AstrBot 安装插件时自动处理，无需额外安装。

---

## ⚙️ 配置项说明

### 账号设置

| 配置项 | 类型 | 默认 | 说明 |
|--------|------|------|------|
| `accounts` | template_list | `[]` | Z-Library 账号池，每个条目一个账号。推荐填 `remix_userid`+`remix_userkey`（成对），或 `email`+`password` |
| `accounts[].name` | string | `account` | 账号备注名（用于日志区分） |
| `accounts[].email` | string | `""` | 账号邮箱（与 password 成对） |
| `accounts[].password` | string | `""` | 账号密码 |
| `accounts[].remix_userid` | string | `""` | remix_userid（与 remix_userkey 成对，推荐） |
| `accounts[].remix_userkey` | string | `""` | remix_userkey（推荐） |

### 连接设置

| 配置项 | 类型 | 默认 | 说明 |
|--------|------|------|------|
| `domain` | string | `z-library.sk` | Z-Library E-API 域名。域名可能被没收/变动，失效时在此更换。**无需带 `https://` 前缀**，插件会自动处理（常见问题 Q3 有验证方法） |
| `proxy` | string | `""` | HTTP 代理（可选）。服务器在国内访问 Z-Library 时需要，如 `http://127.0.0.1:7897`；代理需账号密码时用 `http://用户名:密码@IP:端口` |

### 搜索设置

| 配置项 | 类型 | 默认 | 说明 |
|--------|------|------|------|
| `search_limit` | int | `5` | 搜索默认返回条数上限（LLM 不指定时使用，范围 1-8） |

### 快速配置模板

在 WebUI 配置面板填写，或参考以下结构（`data/config/astrbot_plugin_zlibrary_assistant_config.json`）：

```json
{
  "accounts": [
    {
      "name": "account",
      "email": "",
      "password": "",
      "remix_userid": "",
      "remix_userkey": ""
    }
  ],
  "domain": "z-library.sk",
  "proxy": "",
  "search_limit": 5
}
```

---

## 🛠️ LLM 可调用工具

插件注册 3 个 LLM 工具，模型会自动判断何时调用，你只需用自然语言说需求：

```
用户: 帮我找一本马克思的《资本论》
🤖 → zlib_search_books(query=资本论)
    搜索「资本论」命中 N 本，卡片图已发送 ✅

用户: 下载第 1 本
🤖 → zlib_download_book(book_id=123456)
    下载完成 ✅ 已发送 PDF 文件，账号剩余额度 9 次

用户: 今天还能下载几本书？
🤖 → zlib_get_status()
    Z-Library 账号池状态：account1 ✅ 正常，今日下载 1/10 次
```

### zlib_search_books
关键词搜索 Z-Library 图书库，返回卡片图片 + 带 id 的文本列表。搜索**不消耗**下载额度。

| 参数 | 类型 | 说明 |
|------|------|------|
| `query` | string | **必填**，搜索关键词（书名/作者/主题，支持中英文） |
| `limit` | int? | 返回条数上限（不传则用配置 `search_limit`，最大 8） |
| `language` | string? | 语言过滤，如 `zh` / `en` / `fr` |
| `extension` | string? | 格式过滤，如 `pdf` / `epub` / `mobi` |

### zlib_download_book
按搜索结果 id 下载书籍并保存到本地。**仅在用户明确要求下载时调用**，消耗账号每日额度。

| 参数 | 类型 | 说明 |
|------|------|------|
| `book_id` | int | **必填**，书籍 id（来自 zlib_search_books 返回的编号列表） |

### zlib_get_status
查询账号池各账号登录状态与今日剩余下载额度。

| 参数 | 类型 | 说明 |
|------|------|------|
| （无参数） | - | - |

---

## 🧩 架构

### eapi 客户端（zlib_client.py）
Z-Library 安卓客户端内部接口（非官方 E-API）的异步封装：
- 基于 aiohttp，符合 AstrBot 开发规范（异步、禁 requests）
- 账号池：每账号独立额度状态，下载自动挑选剩余额度最多者
- 错误分类：`rate_limited` / `auth_failed` / `quota_exhausted` / `domain_invalid` / `network_error` / `api_error`
- 书籍缓存持久化到磁盘，AstrBot 重启后仍可凭 id 直接下载
- mojibake 修复：Z-Library 返回的双重编码文本自动还原
- 域名自动规范化（剥掉 `http(s)://` 前缀）；下载文件名自动清洗 Windows 非法字符

### 工具层（tools/）
三个 `FunctionTool` 通过 `add_llm_tools` 注册，LLM 在对话中自动识别调用：
- `search_tool.py` — 搜索 + HTML 卡片渲染（渲染失败自动降级纯文本）
- `download_tool.py` — 按 id 下载到 `<data>/zlibrary_assistant/books/`，返回绝对路径
- `status_tool.py` — 账号池健康度查询

### 卡片渲染（templates.py）
HTML + Jinja2 模板，走 AstrBot 内置文转图（`html_renderer`）：
- CSS 字体栈，云端渲染自动选择中文字体，不依赖本地字体
- 封面由插件端下载并 **base64 内嵌**（云端渲染器无需访问 Z-Library 外链），下载失败自动露出渐变占位
- 标题两行截断、作者单行省略，长列表排版稳定

---

## 🔧 常见问题

### Q1：Docker 部署时搜索结果图片里的中文变成方块/乱码？

**原因**：AstrBot 的"文转图"有两条路径——云端渲染（默认，字体在云端，无需本地字体）和本地渲染（云端失败时自动回退，用 Pillow 渲染）。本地渲染需要系统中文字体，而 AstrBot 官方镜像（`python:3.12-slim`）**没有预装任何中文字体**，Pillow 找不到字体就会用内置位图字体，中文全变方块。

**解决**（任选其一）：
1. Dockerfile 中加一行（推荐，重建镜像生效）：
   ```dockerfile
   RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk
   ```
2. 运行中的容器直接安装：
   ```bash
   docker exec -it <容器名> bash -c "apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk"
   ```
3. 不装包，把任意中文字体文件（如微软雅黑 `msyh.ttc`）放进 AstrBot 的 `data/` 目录并命名为 `font.ttf`：
   ```bash
   docker cp msyh.ttc <容器名>:/AstrBot/data/font.ttf
   ```

### Q2：提示"Z-Library 对当前 IP 限流了"？

Z-Library 对访问频率有严格风控（错误码 `#ipd3`），共享代理 IP（如机场节点）容易被标记。解决：
- 更换代理节点（Clash/V2Ray 切换节点）
- 或减少短时间内的请求频率
- 插件已把该错误分类为友好提示，不会崩溃，等冷却后自动恢复

### Q3：提示"当前域名可能已失效"？

Z-Library 域名经常被没收/变动（实测常用域名中约一半已失效）。解决：在插件配置的 `domain` 项更换为当前可用域名（如 `z-library.sk`，**无需带 `https://` 前缀**）。可用性可先访问 `https://<域名>/eapi/info` 验证（返回 JSON 即有效）。

### Q4：下载提示"账号池额度已用完"？

Z-Library 免费账号每日下载次数有限（约 10 次/天）。解决：
- 等待次日额度重置
- 在 `accounts` 配置中添加更多账号（账号池自动轮换到剩余额度最多的账号）

### Q5：登录一直提示"IP 限流"（#ipd3），但网页能访问？

**原因**：Z-Library 对 **login 端点**有独立风控（比只读端点严格），机场共享 IP 很容易被标记；`/eapi/info` 等只读接口仍可访问，但 `email+password` 登录被拒。

**解决**：改用 **`remix_userid` + `remix_userkey`** 方式配置账号（不走 login 端点，只做 GET 验证，基本不受该风控影响）：
1. 任意时候成功登录过一次 Z-Library 后，在个人页/客户端获取这两个值
2. 在插件配置 `accounts` 中填 `remix_userid` 和 `remix_userkey`（留空 email/password）
3. 插件将用 GET `/eapi/user/profile` 验证并正常使用搜索/下载

> 提示：`remix_userkey` 长期有效，配置一次即可长期使用。

### Q6：下载完成但发不出文件，提示 "Sandbox runtime is disabled"？

**原因**：AstrBot 的 `send_message_to_user` 发送本地文件依赖 **Computer Use 本地运行时**（`computer_use_runtime`），而 AstrBot 默认是 `none`，导致本地文件被沙盒拦截。

**解决**（WebUI 或配置文件二选一）：
1. **WebUI**：`配置（Config）→ 服务提供商设置 → computer_use_runtime` 改为 `local`（并可将 `computer_use_require_admin` 设为 `false`）
2. **配置文件**：编辑 `data/config/astrbot_config.json`（不存在则创建）：
   ```json
   {
     "provider_settings": {
       "computer_use_runtime": "local",
       "computer_use_require_admin": false
     }
   }
   ```
   保存后重启 AstrBot。

### Q7：需要配置哪些依赖？

插件仅依赖 `aiohttp` + `aiofiles`（异步 HTTP/文件库），AstrBot 安装插件时自动处理。文转图使用 AstrBot 内置能力，无需额外安装。

### Q8：Docker 部署时搜索很慢 / 卡片封面全是占位 / 工具报 timeout？

**原因**：Docker 容器是独立网络环境，配置里填 `http://127.0.0.1:7897` 指向的是**容器自己**（里面没有你的代理），封面下载全部失败（显示占位），且云端文转图在容器内可能连不上，渲染拖满整个工具超时（AstrBot 默认 `tool_call_timeout` 60 秒）。

**解决**：
1. **代理填容器能访问到的地址**：格式 `http://IP:端口`；代理需要账号密码时用 `http://用户名:密码@IP:端口`（如 `http://slandre:hj282102338@astrbot:7080`），插件基于 aiohttp，自动发送 `Proxy-Authorization` 认证头。Windows/Mac Docker Desktop 可用 `http://host.docker.internal:端口`；Linux 用宿主机局域网 IP，并确保代理软件开启了「允许局域网连接」
2. 若云端文转图不可达：插件已内置**25 秒渲染超时保护**，渲染失败会自动降级为纯文本书单（不会整个工具报错），可接受的话无需处理
3. 仍嫌时间紧：在 AstrBot 配置 `provider_settings.tool_call_timeout` 调大（如 `120`）

---

## 📝 更新日志

> 📋 **[查看完整更新日志 →](CHANGELOG.md)**

---

## ⭐ 支持本项目

如果这个插件对你有帮助，欢迎点亮 Star ⭐，有问题和建议请提交 [Issue](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/issues) 或 [Pull Request](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/pulls)。

## 🙏 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) 开源聊天机器人框架

---

## 📜 许可证

本项目采用 **MIT License** 开源协议。

---

## 👤 作者

[@OMSociety](https://github.com/OMSociety)
