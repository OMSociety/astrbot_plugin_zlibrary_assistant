# 更新日志

## v1.0.0 (2026-08-15)

首个正式版本，LLM 自动调用全链路实测通过（搜索 → 卡片图 → 下载 → 文件发送）。

### 功能
- **zlib_search_books**：关键词搜索 Z-Library（3300 万+ 本书），返回 HTML 卡片图片（封面/标题/作者/格式/大小）+ 带 id 文本列表；支持语言/格式过滤；搜索不耗额度
- **zlib_download_book**：按 id 下载书籍（账号池自动轮换、额度预检），保存到 `<data>/zlibrary_assistant/books/`，LLM 用 send_message_to_user 发送文件
- **zlib_get_status**：账号池登录状态与每日下载额度查询

### 特性
- 账号池：多账号配置（template_list），每个账号独立每日额度（约 10 次/天），下载自动选剩余额度最多者，耗尽拒绝并提示
- 两种凭据方式：`email+password`（自动换取 remix 凭证）或 `remix_userid+remix_userkey`（GET 验证，绕过 login 端点风控）
- HTML 卡片：封面加载失败自动降级渐变占位；标题/作者行截断
- 错误分类：IP 限流 / 域名失效 / 登录失效 / 额度耗尽 / 网络异常，全部友好中文提示
- 书籍缓存持久化：AstrBot 重启后仍可凭 id 下载
- mojibake 修复：Z-Library 双重编码文本自动还原
- 域名/代理可配置（域名变动无需改代码）

### 修复
- pydantic 2.13 dataclass 兼容（`model_config` 属性改为装饰器 `config=` 参数）
- 搜索工具兼容纯文本模型（图片改文本+路径返回，不再强发 `image_url`）
- aiohttp 强制 UTF-8 解码 + 递归 mojibake 修复
- 异步文件写入（aiofiles）

### 维护更新（同日全量检查）
- `requirements.txt` 补声明 `aiofiles`（此前仅声明 aiohttp，干净环境安装插件会因缺依赖报错）
- 域名配置规范化：自动剥掉 `http(s)://` 协议前缀与多余斜杠（防止从浏览器复制的带协议域名拼出无效 URL）
- 下载文件名清洗：替换 Windows 非法字符（`\ / : * ? " < > |`）与控制字符，避免保存文件失败
- 移除 `@register` 装饰器：插件标识统一由 `metadata.yaml` 驱动（消除装饰器与 metadata 的 name/version 不一致）
- `initialize()` 增加防重入守卫；删除 `_request_json` 死参数 `allow_html`；ruff 规范化导入排序；`.gitignore` 排除 `.ruff_cache/`
- **搜索结果封面修复**：封面在插件端下载并转 base64 内嵌进卡片 HTML（云端文转图服务访问不了 Z-Library 封面 CDN 外链，此前实测封面全部显示占位）
