<p align="center"><strong>English</strong> · <a href="README.md">中文</a> · <a href="README_ru.md">Русский</a> · <a href="README_ja.md">日本語</a></p>

<div align="center">

<img src="https://raw.githubusercontent.com/OMSociety/astrbot_plugin_zlibrary_assistant/main/logo.png" width="120" alt="ZLibrary Assistant Logo" />

# Zlibrary Assistant

**Z-Library book search & download assistant** — book search · one-click download · account pool rotation · HTML card results · quota management

[![Version](https://img.shields.io/badge/version-1.1.1-blue.svg)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant)
[![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A5v4-green.svg)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/OMSociety/astrbot_plugin_zlibrary_assistant)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/stargazers)
[![Issues](https://img.shields.io/github/issues/OMSociety/astrbot_plugin_zlibrary_assistant)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/issues)

</div>

> This project was written by AI

---

## Core Features

| Feature | Description |
|------|------|
| **Book Search** | Keyword search on Z-Library (33 million+ books), returns an HTML card image (cover/title/author/format/size) + a text list with ids |
| **One-Click Download** | Just say the word and the file is sent to the conversation automatically; supports pdf / epub / mobi and more |
| **Account Pool Rotation** | Multiple accounts share the daily download quota; the account with the most remaining quota is picked automatically, and a friendly message is shown when exhausted |
| **Dual Credential Methods** | `remix_userid+remix_userkey` (bypasses login endpoint risk control) or `email+password` (automatically exchanged for remix credentials) |
| **HTML Card Results** | Search results are rendered into uniform card images; failed cover loads automatically fall back to a gradient placeholder, so cards are never blank |
| **Error Classification** | IP rate limiting / invalid domain / expired login / exhausted quota / network errors are all handled by category with friendly messages; the plugin does not crash |

---

## Feature Overview

### Book Search
Just say in the chat what book you are looking for; the LLM automatically calls the search tool and returns an **HTML card image** (cover/title/author/format/size) plus a text list with ids:

```
User: Find "Das Kapital" by Karl Marx
🤖 → zlib_search_books(query=Das Kapital)
    Found N matches for "Das Kapital", card image sent ✅
```

### Book Download
Say the word and it downloads; the file is sent to the conversation automatically:

```
User: Download book #1
🤖 → zlib_download_book(book_id=<id from search results>)
    Download complete ✅ PDF file sent, account quota remaining: 9
```

### Account Pool & Quota Management
- Configure multiple Z-Library accounts, each with an **independent daily download quota** (about 10/day for free accounts)
- On download, it automatically rotates to the account with the **most quota left today**; when all are exhausted, the request is declined with a friendly message
- Two credential methods are supported: `email+password` or `remix_userid+remix_userkey`

### HTML Card Results
Search results are rendered into uniform card images (Z-Library cover + metadata badges); failed cover loads automatically fall back to a gradient placeholder, so cards are never blank.

### Error Classification
IP rate limiting / invalid domain / expired login / exhausted quota / network errors are all handled by category and translated into friendly messages; a single error never crashes the plugin.

---

## Quick Start

### Step 1: Installation

AstrBot WebUI → Plugin Marketplace → install `astrbot_plugin_zlibrary_assistant` from GitHub

### Step 2: Minimal Configuration (get search & download running)

Just add one account under `accounts` on the plugin configuration page in the WebUI (remix method recommended):

1. Open the plugin configuration page → click add entry under `accounts`
2. **Recommended method**: fill in `remix_userid` + `remix_userkey` (after logging into z-library.sk in your browser, press F12 → copy from Cookies; valid long-term and unaffected by login endpoint risk control)
3. Or fill in `email` + `password` (remix credentials are exchanged automatically after login)
4. To use multiple accounts, keep adding entries (the account pool rotates automatically)

After saving, restart AstrBot and you can search and download books directly in the conversation.

> **Note:** Servers in mainland China need a proxy in `proxy` (e.g. `http://127.0.0.1:7897`); servers outside China can leave it empty. If you want to use a stable onion entrance, see the [Tor/onion Deployment](#toronion-deployment).

### Dependencies
The plugin depends on `aiohttp` + `aiofiles` + `aiohttp-socks` + `Pillow`; AstrBot handles them automatically when installing the plugin, no extra steps needed.

---

## Tor/onion Deployment

Optionally, the plugin can reach the Z-Library onion E-API through Tor while keeping full backward compatibility with the existing clearnet domain and HTTP proxy settings. It provides the same 3 LLM tools:

- `zlib_search_books`: search books
- `zlib_download_book`: download by search-result id
- `zlib_get_status`: check accounts and quota (admin sessions only)

Network requests enter Tor via SOCKS5, and `.onion` DNS resolution happens on the Tor side. The plugin provides no arbitrary-URL browsing tool, so a prompt injection cannot turn the bot into an open proxy.

### Docker deployment

1. Install `astrbot_plugin_zlibrary_assistant` as usual.
2. Merge the following `tor` service into AstrBot's `docker-compose.yml` (do not publish port 9050 to the public internet):

```yaml
services:
  tor:
    build: ./data/plugins/astrbot_plugin_zlibrary_assistant/docker/tor
    restart: unless-stopped
    # If Tor needs to reach the public internet through your existing proxy, enable according to its actual type:
    # environment:
    #   - TOR_UPSTREAM_PROXY=http://proxy-host:port
```

3. Make sure AstrBot and `tor` share the same Compose network, then rebuild and start:

```bash
docker compose up -d --build tor astrbot
```

4. Fill in the plugin configuration in AstrBot:

```json
{
  "domain": "http://loginzlib2vrak5zzpcocc3ouizykn6k5qecgj2tzlnab5wcbqhembyd.onion",
  "proxy": "socks5://tor:9050",
  "max_download_mb": 80,
  "search_limit": 5,
  "accounts": []
}
```

A plain HTTP/SOCKS proxy is not an onion router and cannot replace Tor. The plugin always connects to `socks5://tor:9050`; if Tor itself needs an upstream proxy, configure `TOR_UPSTREAM_PROXY=http://proxy-host:port` or `TOR_UPSTREAM_PROXY=socks5://proxy-host:port` according to the actual protocol of your existing service. Fill in the upstream service's name, address, and port according to your actual deployment.

Account configuration is the same as before. `remix_userid` + `remix_userkey` is recommended to avoid frequent calls to the login endpoint.

### Non-Docker deployment

Install and start Tor with its SOCKS port listening on localhost only, then set `proxy` to `socks5://127.0.0.1:9050`. If AstrBot runs in Docker and Tor on the host, Docker Desktop usually uses `socks5://host.docker.internal:9050`; you also need Tor to listen on an interface reachable from Docker and to restrict sources with a firewall.

### Security constraints

- Only HTTP/HTTPS is accepted; `.onion` must be a 56-character v3 address.
- `.onion` requests must go through a SOCKS proxy; local DNS resolution is forbidden.
- Every hop of API-returned redirects is re-checked by the SSRF validation.
- A single download defaults to at most 80 MiB to prevent abnormal responses from exhausting memory.
- The Tor service does not publish host ports; it is only for the internal Compose network.

### Verification

```bash
python -m pytest -q
```

Actual connectivity also depends on whether Tor has finished bootstrapping, whether the target onion is online, and whether the account credentials are valid.

---

## Configuration Reference

### Account Settings

| Configuration Key | Type | Default | Description |
|--------|------|------|------|
| `accounts` | template_list | `[]` | Z-Library account pool, one account per entry. Recommended: `remix_userid`+`remix_userkey` (as a pair), or `email`+`password` |
| `accounts[].name` | string | `account` | Account label (used to distinguish accounts in logs) |
| `accounts[].email` | string | `""` | Account email (paired with password) |
| `accounts[].password` | string | `""` | Account password |
| `accounts[].remix_userid` | string | `""` | remix_userid (paired with remix_userkey, recommended) |
| `accounts[].remix_userkey` | string | `""` | remix_userkey (recommended) |

### Connection Settings

| Configuration Key | Type | Default | Description |
|--------|------|------|------|
| `domain` | string | `z-library.sk` | Z-Library E-API domain or full base URL. Plain domains use HTTPS by default; you can also enter `http://...onion` |
| `proxy` | string | `""` | HTTP or SOCKS proxy (optional). onion addresses must use Tor SOCKS, e.g. `socks5://tor:9050` |
| `max_download_mb` | int | `80` | Maximum size of a single downloaded file (MiB, range 1-512) |

### Search Settings

| Configuration Key | Type | Default | Description |
|--------|------|------|------|
| `search_limit` | int | `5` | Default maximum number of search results (used when the LLM does not specify; range 1-8) |

### Quick Configuration Template

Fill it in on the WebUI configuration panel, or refer to the following structure (`data/config/astrbot_plugin_zlibrary_assistant_config.json`):

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
  "max_download_mb": 80,
  "search_limit": 5
}
```

---

## LLM-Callable Tools

The plugin registers 3 LLM tools; the model decides automatically when to call them — you only need to state your needs in natural language:

```
User: Find "Das Kapital" by Karl Marx
🤖 → zlib_search_books(query=Das Kapital)
    Found N matches for "Das Kapital", card image sent ✅

User: Download book #1
🤖 → zlib_download_book(book_id=123456)
    Download complete ✅ PDF file sent, account quota remaining: 9

User: How many more books can I download today?
🤖 → zlib_get_status()
    Z-Library account pool status: account1 ✅ OK, 1/10 downloads used today
```

### zlib_search_books
Keyword search across the Z-Library book library; returns a card image + a text list with ids. Searching does **not** consume download quota.

| Parameter | Type | Description |
|------|------|------|
| `query` | string | **Required**, search keywords (title/author/topic, Chinese and English supported) |
| `limit` | int? | Maximum number of results (if omitted, the configured `search_limit` is used, max 8) |
| `language` | string? | Language filter, e.g. `zh` / `en` / `fr` |
| `extension` | string? | Format filter, e.g. `pdf` / `epub` / `mobi` |

### zlib_download_book
Downloads a book by its search-result id and saves it locally. **Only called when the user explicitly requests a download**; consumes the account's daily quota.

| Parameter | Type | Description |
|------|------|------|
| `book_id` | int | **Required**, book id (from the numbered list returned by zlib_search_books) |

### zlib_get_status
Queries the login status and remaining daily download quota of each account in the pool. **Admin sessions only** (the result contains account-pool internals).

| Parameter | Type | Description |
|------|------|------|
| (no parameters) | - | - |

---

## Architecture

### eapi client (zlib_client.py)
An async wrapper around the internal interface of the Z-Library Android client (unofficial E-API):
- Based on aiohttp, following AstrBot development conventions (async, no requests)
- Account pool: independent quota state per account; downloads automatically pick the account with the most quota left
- Error classification: `rate_limited` / `auth_failed` / `quota_exhausted` / `domain_invalid` / `network_error` / `api_error`
- Book cache persisted to disk; books can still be downloaded directly by id after AstrBot restarts
- Mojibake repair: double-encoded text returned by Z-Library is automatically restored
- Domain auto-normalization (adds `https://` for scheme-less plain domains and `http://` for onion; addresses with a path are rejected); download filenames are automatically sanitized of Windows-illegal characters

### Tool layer (tools/)
Three `FunctionTool`s registered via `add_llm_tools`; the LLM recognizes and calls them automatically in conversation:
- `search_tool.py` — search + HTML card rendering (automatically falls back to plain text if rendering fails)
- `download_tool.py` — downloads by id to `<data>/zlibrary_assistant/books/` and returns the absolute path
- `status_tool.py` — account pool health query

### Card rendering (templates.py)
HTML + Jinja2 templates, using AstrBot's built-in text-to-image (`html_renderer`):
- CSS font stack; cloud rendering selects Chinese fonts automatically, no dependence on local fonts
- Covers are downloaded by the plugin and **embedded as base64** (the cloud renderer never needs to access Z-Library external links); a gradient placeholder shows automatically if the download fails
- Titles truncated to two lines, authors ellipsized on one line, stable layout for long lists

---

## FAQ

### Q1: In Docker deployments, Chinese characters in search-result card images turn into boxes/mojibake?

**Cause**: AstrBot's "text-to-image" has two paths — cloud rendering (default; fonts live in the cloud, no local fonts needed) and local rendering (automatic fallback when the cloud fails, rendered with Pillow). Local rendering requires Chinese fonts in the system, but the official AstrBot image (`python:3.12-slim`) **has no Chinese fonts preinstalled**; when Pillow cannot find a font it falls back to a built-in bitmap font, and all Chinese becomes boxes.

**Fix** (choose one):
1. Add one line to the Dockerfile (recommended; takes effect after rebuilding the image):
   ```dockerfile
   RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk
   ```
2. Install directly in the running container:
   ```bash
   docker exec -it <container_name> bash -c "apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk"
   ```
3. Without installing packages, put any Chinese font file (e.g. Microsoft YaHei `msyh.ttc`) into AstrBot's `data/` directory and name it `font.ttf`:
   ```bash
   docker cp msyh.ttc <container_name>:/AstrBot/data/font.ttf
   ```

### Q2: It says "Z-Library is rate-limiting the current IP"?

Z-Library applies strict frequency control (error code `#ipd3`); shared proxy IPs (e.g. so-called "airport" proxy-subscription nodes) are easily flagged. Solutions:
- Switch proxy nodes (switch nodes in Clash/V2Ray)
- Or reduce the request frequency over short periods
- The plugin classifies this error into a friendly message, does not crash, and recovers automatically after cooldown

### Q3: It says "the current domain may be invalid"?

Z-Library domains are frequently seized/changed (in practice, about half of the commonly used domains are already dead). Solution: change the `domain` entry in the plugin configuration to a currently working domain (e.g. `z-library.sk`, **no `https://` prefix needed**). You can verify availability by visiting `https://<domain>/eapi/info` first (returning JSON means it works).

### Q4: Downloads say "the account pool quota is used up"?

Free Z-Library accounts have a limited number of daily downloads (about 10/day). Solutions:
- Wait for the quota to reset the next day
- Add more accounts in the `accounts` configuration (the pool automatically rotates to the account with the most quota left)

### Q5: Login keeps reporting "IP rate-limited" (#ipd3), but the website is accessible?

**Cause**: Z-Library has separate risk control on the **login endpoint** (stricter than read-only endpoints); shared "airport" (proxy-subscription) IPs are easily flagged. Read-only interfaces like `/eapi/info` remain accessible, but `email+password` login is rejected.

**Fix**: switch to the **`remix_userid` + `remix_userkey`** method (does not go through the login endpoint, only does a GET verification, essentially unaffected by this risk control):
1. After you have successfully logged into Z-Library at least once, obtain these two values from the profile page/client
2. Fill `remix_userid` and `remix_userkey` in the plugin's `accounts` configuration (leave email/password empty)
3. The plugin will verify via GET `/eapi/user/profile`, and search/download will work normally

> **Tip:** `remix_userkey` is valid long-term; configure it once and it keeps working.

### Q6: Download completes but the file cannot be sent, with "Sandbox runtime is disabled by configuration"?

**Cause**: Sending local files via AstrBot's `send_message_to_user` depends on the **Computer Use local runtime** (`computer_use_runtime`), which defaults to `none` in AstrBot, so local files are blocked by the sandbox.

**Fix** (WebUI or config file, either one):
1. **WebUI**: `Configuration (Config) → Service Provider Settings → computer_use_runtime` change to `local` (you can also set `computer_use_require_admin` to `false`)
2. **Config file**: edit `data/config/astrbot_config.json` (create it if it does not exist):
   ```json
   {
     "provider_settings": {
       "computer_use_runtime": "local",
       "computer_use_require_admin": false
     }
   }
   ```
   Restart AstrBot after saving.

### Q7: Which dependencies need to be configured?

The plugin depends on `aiohttp` + `aiofiles` + `aiohttp-socks` + `Pillow`; AstrBot handles them automatically when installing the plugin. Text-to-image uses AstrBot's built-in capability, nothing extra to install.

### Q8: In Docker deployments, search is slow / all card covers are placeholders / the tool reports timeout?

**Cause**: A Docker container is an isolated network environment; `http://127.0.0.1:7897` in the configuration points to **the container itself** (your proxy is not inside it), so all cover downloads fail (placeholders are shown), and the cloud text-to-image may be unreachable from within the container, rendering eating up the entire tool timeout (AstrBot default `tool_call_timeout` is 120 seconds).

**Fix**:
1. **Fill in a proxy address the container can reach**: format `http://IP:port`; if the proxy requires credentials, use `http://username:password@IP:port` (e.g. `http://user:pass@IP:port`); the plugin is based on aiohttp and automatically sends the `Proxy-Authorization` authentication header. On Windows/Mac Docker Desktop you can use `http://host.docker.internal:port`; on Linux use the host's LAN IP, and make sure the proxy software enables "allow LAN connections"
2. If the cloud text-to-image is unreachable: the plugin has a built-in **25-second rendering timeout protection**; on rendering failure it automatically falls back to a plain-text book list (the whole tool does not error out), so no action is needed if that is acceptable
3. If time is still tight: increase AstrBot's `agent_runner.config.misc.tool_call_timeout` (e.g. `240`)

## Changelog

> **[View the full changelog →](CHANGELOG.md)**

## Support & Acknowledgements

If this plugin helps you, please consider giving it a Star; for issues and suggestions, feel free to open an [Issue](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/issues) or a [Pull Request](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/pulls).

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) open-source chatbot framework

## License & Author

This project is released under the **MIT License**.

[@OMSociety](https://github.com/OMSociety)
