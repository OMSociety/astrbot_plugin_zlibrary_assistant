# Tor/onion 部署指南

插件可选通过 Tor 访问 Z-Library onion E-API，并保留原有明网域名和 HTTP 代理配置的向后兼容。它提供原有的 3 个 LLM 工具：

- `zlib_search_books`：搜索图书
- `zlib_download_book`：按搜索结果 id 下载
- `zlib_get_status`：查看账号和额度

网络请求通过 SOCKS5 进入 Tor，`.onion` DNS 解析在 Tor 端完成。插件不提供任意 URL 浏览工具，避免 Bot 被提示注入后变成开放代理。

## Docker 部署

1. 正常安装 `astrbot_plugin_zlibrary_assistant`。
2. 把下面的 `tor` 服务合并到 AstrBot 的 `docker-compose.yml`（不要映射 9050 到公网）：

```yaml
services:
  tor:
    build: ./data/plugins/astrbot_plugin_zlibrary_assistant/docker/tor
    restart: unless-stopped
    # 如 Tor 需经过现有代理连接公网，再按实际类型启用：
    # environment:
    #   - TOR_UPSTREAM_PROXY=http://proxy-host:port
```

3. 确保 AstrBot 和 `tor` 在同一个 Compose 网络，重建并启动：

```bash
docker compose up -d --build tor astrbot
```

4. 在 AstrBot 插件配置中填写：

```json
{
  "domain": "http://loginzlib2vrak5zzpcocc3ouizykn6k5qecgj2tzlnab5wcbqhembyd.onion",
  "proxy": "socks5://tor:9050",
  "max_download_mb": 80,
  "search_limit": 5,
  "accounts": []
}
```

普通 HTTP/SOCKS 代理不是 onion 路由器，不能代替 Tor。插件始终连接
`socks5://tor:9050`；如 Tor 本身需要上游代理，再依现有服务的实际协议配置
`TOR_UPSTREAM_PROXY=http://proxy-host:port` 或
`TOR_UPSTREAM_PROXY=socks5://proxy-host:port`。上游服务的名称、地址和端口均按实际部署填写。

账号配置与原插件相同。推荐 `remix_userid` + `remix_userkey`，避免频繁调用登录端点。

## 非 Docker 部署

先安装并启动 Tor，使 SOCKS 端口只监听本机，然后将 `proxy` 设为 `socks5://127.0.0.1:9050`。如果 AstrBot 在 Docker、Tor 在宿主机，Docker Desktop 通常使用 `socks5://host.docker.internal:9050`，同时需要让 Tor 监听 Docker 可达的接口并用防火墙限制来源。

## 安全约束

- 只接受 HTTP/HTTPS；`.onion` 必须是 56 字符的 v3 地址。
- `.onion` 请求必须配置 SOCKS 代理，禁止本机 DNS 解析。
- API 返回的重定向每一跳都重新做 SSRF 校验。
- 单文件默认最多 80 MiB，避免异常响应耗尽内存。
- Tor 服务不发布宿主机端口，仅供 Compose 内部网络使用。

## 验证

```bash
python -m pytest -q
```

实际连通性还取决于 Tor 是否完成 bootstrap、目标 onion 是否在线，以及账号凭据是否有效。
