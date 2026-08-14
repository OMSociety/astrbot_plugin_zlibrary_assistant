# ZLibrary Assistant（AstrBot 插件）

为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供 **Z-Library 图书搜索与下载**能力的 LLM 工具集插件。

无需任何手动指令——用户正常聊天说"帮我找一本关于 XX 的书"，接入的 LLM 会自动调用本插件的工具完成搜索、返回结果图片、按需下载并发送文件。

## ✨ 功能

| 工具 | 作用 |
|---|---|
| `zlib_search_books` | 关键词搜索 Z-Library 图书库（3300 万+ 本书），返回**HTML 卡片图片**（封面/标题/作者/年份/语言/格式/大小）+ 带 id 的文本列表。搜索不消耗下载额度 |
| `zlib_download_book` | 按搜索结果 id 下载书籍。**仅在用户明确要求时由 LLM 调用**，自动轮换到今日剩余额度最多的账号 |
| `zlib_get_status` | 查询账号池各账号登录状态与今日剩余下载额度 |

## 📦 安装

1. 在 AstrBot WebUI 的插件管理页，通过 GitHub 仓库安装：
   `https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant`
2. 或手动放入 `AstrBot/data/plugins/` 目录后重启 AstrBot
3. 在 WebUI 插件配置页填写账号池等配置（见下）

## ⚙️ 配置

| 配置项 | 说明 |
|---|---|
| `accounts` | **账号池**（template_list，可添加多个账号）。每个账号支持两种填法：`email+password`（登录后自动换取 remix 凭证），或 `remix_userid+remix_userkey`（在 Z-Library 个人页获取）。**每个账号每日独立下载额度**（免费约 10 次/天） |
| `domain` | Z-Library E-API 域名，默认 `z-library.sk`。域名可能被没收/变动，失效时在此更换 |
| `proxy` | HTTP 代理（可选）。服务器在国内访问 Z-Library 时需要，如 `http://127.0.0.1:7897` |
| `search_limit` | 搜索默认返回条数上限（默认 5） |

## 💬 使用示例（LLM 自动完成，无需指令）

```
用户：帮我找一本马克思的《资本论》
LLM：→ 自动调用 zlib_search_books(query="资本论", language="zh")
     → 返回卡片图 + 书籍列表
     → 把卡片图发送到群里，列出前几本

用户：下载第 1 本
LLM：→ 自动调用 zlib_download_book(book_id=xxx)
     → 下载完成，通过 send_message_to_user 发送 PDF 文件 + 剩余额度提示
```

## 🛠 工作原理

- 基于 Z-Library 的 **E-API**（安卓客户端内部接口，非官方），端点文档见 [baroxyton/zlibrary-eapi-documentation](https://github.com/baroxyton/zlibrary-eapi-documentation)
- 异步客户端（aiohttp）自研，符合 AstrBot 开发规范（禁止 requests）
- 结果卡片用 AstrBot 内置文转图（`html_renderer`）渲染 HTML+Jinja2 模板
- 错误分级：IP 限流 / 域名失效 / 登录失效 / 额度耗尽 / 网络异常，全部翻译为友好中文提示

## ❓ 常见问题（Q&A）

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

Z-Library 域名经常被没收/变动（实测常用域名中约一半已失效）。解决：在插件配置的 `domain` 项更换为当前可用域名（如 `z-library.sk`、`singlelogin.re`）。可用性可先访问 `https://<域名>/eapi/info` 验证（返回 JSON 即有效）。

### Q4：下载提示"账号池额度已用完"？

Z-Library 免费账号每日下载次数有限（约 10 次/天）。解决：
- 等待次日额度重置
- 在 `accounts` 配置中添加更多账号（账号池自动轮换到剩余额度最多的账号）

### Q5：需要配置哪些依赖？

插件仅依赖 `aiohttp`（异步 HTTP 库），AstrBot 安装插件时自动处理。文转图使用 AstrBot 内置能力，无需额外安装。

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

### Q7：登录一直提示"IP 限流"（#ipd3），但网页能访问？

**原因**：Z-Library 对 **login 端点**有独立风控（比只读端点严格），机场共享 IP 很容易被标记；`/eapi/info` 等只读接口仍可访问，但 `email+password` 登录被拒。

**解决**：改用 **`remix_userid` + `remix_userkey`** 方式配置账号（不走 login 端点，只做 GET 验证，基本不受该风控影响）：
1. 任意时候成功登录过一次 Z-Library 后，在个人页/客户端获取这两个值
2. 在插件配置 `accounts` 中填 `remix_userid` 和 `remix_userkey`（留空 email/password）
3. 插件将用 GET `/eapi/user/profile` 验证并正常使用搜索/下载

> 提示：`remix_userkey` 长期有效，配置一次即可长期使用。

## 📄 开发状态

- v0.1.0：搜索 + 下载 + 状态工具，账号池，HTML 卡片，错误分类
- 后续规划：下载格式选择、多语言过滤增强、缓存命中提示

## ⚠️ 声明

本插件调用的是 Z-Library 的非官方 E-API，仅供学习与技术研究。请遵守当地法律法规与版权规定，合理使用。
