<p align="center"><a href="README.md">中文</a> · <strong>English</strong> · <a href="README_ru.md">Русский</a> · <a href="README_ja.md">日本語</a></p>

<div align="center">

<h1><img src="https://raw.githubusercontent.com/OMSociety/astrbot_plugin_zlibrary_assistant/main/logo.png" width="192" alt="Zlibrary Assistant"><br>Zlibrary Assistant</h1>

**Z-Library book search and download assistant** — automatic mirror failover · `/zlib` commands · card-image results · account pool with quota control

[![Version](https://img.shields.io/github/v/tag/OMSociety/astrbot_plugin_zlibrary_assistant?label=version&color=blue)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/releases)
[![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A5v4-green.svg)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/OMSociety/astrbot_plugin_zlibrary_assistant)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/stargazers)
[![Issues](https://img.shields.io/github/issues/OMSociety/astrbot_plugin_zlibrary_assistant)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/issues)

</div>

Zlibrary Assistant brings Z-Library search and download to AstrBot. It carries a built-in set of mirror domains with automatic failover, works through both `/zlib` commands and LLM tools, shows search results as card images, and downloads through an account pool that rotates by remaining quota.

---

## Quick start

Search for "Zlibrary助手" in the AstrBot WebUI plugin market and install it.

### Minimal configuration

1. Search only: no configuration needed (set `proxy` if the server cannot reach the mirrors directly).
2. To download: add one account under `accounts`; `remix_userid` + `remix_userkey` are recommended. Log in to Z-Library in a browser, press F12 and copy both values from Application → Cookies; they stay valid for a long time and avoid the login endpoint, which risk control often rejects. `email` + `password` also works.
3. Save, reload the plugin, then send the bot:

```
/zlib search Das Kapital -l en
/zlib download 1
```

> **Note**: the official domains require browser verification that some egress IPs cannot pass, in which case the plugin switches to the built-in mirrors automatically; `/zlib diag` shows which mirrors are reachable right now.

---

## Preview

What `/zlib search 资本论 -l zh` actually returns; the card footer shows how to ask for details, download and paging:

![Search result card preview](docs/card-preview.jpg)

---

## Commands

Command group `/zlib`, alias `/zl`. The sub-commands also accept Chinese names (`搜索`, `下一页`, `上一页`, `下载`, `状态`, `诊断`, `帮助`).

| Command | Description |
|---------|-------------|
| `/zlib search keywords [-l language] [-e extension] [-n count]` | Search, e.g. `/zlib search Das Kapital -l en -e pdf`. Aliases `s`, `search` |
| `/zlib next` / `/zlib prev` | Page through the latest search. Aliases `n`, `p` |
| `/zlib detail index-or-id` | Full information for one book: publisher, pages, series, categories and description. Aliases `d`, `detail` |
| `/zlib download index-or-id` | The index refers to the latest results of the current session; a number larger than the page size is treated as a book id. Aliases `dl`, `download` |
| `/zlib status` | Account login state and today's quota (admins only) |
| `/zlib diag` | Probes every mirror for reachability and latency and checks account logins (admins only) |
| `/zlib help` | Command help |

The language option accepts `zh`, `en`, `chinese`, `english` and similar. Searching does not spend download quota; with `download_admin_only` on, only admins may download.

---

## LLM tools

| Tool | Parameters | Description |
|------|------------|-------------|
| `zlib_search_books` | `query` (required), `limit`, `page`, `language`, `extension` | Search; the plugin sends the result card itself and hands the model a text list with ids and descriptions |
| `zlib_download_book` | `book_id` (required) | Download by search-result id; called only when the user explicitly asks; returns as soon as the download is queued, and the plugin sends both the finished file and any failure reason itself |
| `zlib_get_status` | none | Account pool status; admin sessions only |

```
User: Find me Marx's "Das Kapital", English PDF
Model calls: zlib_search_books(query="Das Kapital", language="en", extension="pdf")
User: Download the first one
Model calls: zlib_download_book(book_id=<id from the search results>)
```

Fence markers are stripped from book fields and the fields are truncated before they enter a tool result, reducing the risk of instructions hidden in a title. Messages a tool call needs to deliver (the result card, the downloaded file) are sent by the plugin itself, so the model only describes the outcome and never handles local paths.

---

## Mirrors and failover

- Built-in domains are tried in this order initially, then re-ranked automatically by observed results (recently working ones first, lower latency preferred): the official entries `z-library.sk`, `z-lib.sk`, `z-library.im`, `z-library.hn` first, then the mirrors `z-library.website`, `z-lib.gd`, `z-lib.gl`, `article.sk`, `articles.sk`, `z-library.qa`, `zlib.bz`.
- When the domain pool has no recently working mirror (just started, or idle for a while), the first search probes every mirror concurrently (at most 10 seconds each) and writes the result into the pool, shared by all sessions; searches that arrive during the probe share that single probe, and when no mirror is reachable the next probe waits at least 60 seconds. Background warm-up probes once as well.
- Requests carry the `source: android` header to the mirrors' E-API. Network errors, certificate errors, browser-verification blocks, Cloudflare blocks and rate limiting trigger failover and put that mirror on a cooldown that depends on the error type.
- Domains in `domains` have the highest priority, followed by the built-in mirrors; domains learned from a mirror come last and serve only requests without account credentials.
- Whether a mirror is reachable depends on your egress network; rely on the result of `/zlib diag`.

---

## Configuration

### Account settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `accounts` | template_list | `[]` | Account pool, one entry per account; when both remix and email/password are set, remix wins |
| `accounts[].label` | string | `account` | Account label for logs and status |
| `accounts[].remix_userid` | string | `""` | remix_userid (recommended, paired with remix_userkey) |
| `accounts[].remix_userkey` | string | `""` | remix_userkey (recommended) |
| `accounts[].email` | string | `""` | Account email (fallback, paired with the password) |
| `accounts[].password` | string | `""` | Account password (fallback) |

### Connection settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `domains` | list | `[]` | Extra mirror domains, host names only; onion addresses need a SOCKS proxy |
| `proxy` | string | `""` | HTTP or SOCKS proxy, e.g. `http://127.0.0.1:7890` |
| `max_download_mb` | int | `80` | Maximum size of one download (MiB, 1-512) |

### Search and download settings

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `search_limit` | int | `5` | Default number of search results (1-10) |
| `search_timeout` | int | `30` | Timeout for search, paging and quota checks (seconds, 5-300); raise it on poor networks or behind a proxy |
| `card_enabled` | bool | `true` | Show search results as a card image; with it off, or if rendering fails, a text list is sent |
| `download_admin_only` | bool | `false` | Only admins may download |

### Quick configuration template

`data/config/astrbot_plugin_zlibrary_assistant_config.json`:

```json
{
  "accounts": [
    {"label": "account1", "remix_userid": "", "remix_userkey": "", "email": "", "password": ""}
  ],
  "domains": [],
  "proxy": "",
  "max_download_mb": 80,
  "search_limit": 5,
  "search_timeout": 30,
  "card_enabled": true,
  "download_admin_only": false
}
```

---

## Tor/onion deployment

The plugin can reach an onion entry through Tor: a `.onion` domain requires a SOCKS proxy, and DNS resolution happens on the Tor side. The plugin offers no arbitrary-URL browsing, so a prompt-injected bot cannot act as an open proxy.

### Docker

1. Merge this `tor` service into AstrBot's `docker-compose.yml` (do not publish port 9050):

```yaml
services:
  tor:
    build: ./data/plugins/astrbot_plugin_zlibrary_assistant/docker/tor
    restart: unless-stopped
    # Enable only if Tor must go through an existing proxy:
    # environment:
    #   - TOR_UPSTREAM_PROXY=http://proxy-host:port
```

2. Put AstrBot and `tor` on the same Compose network, then run `docker compose up -d --build tor astrbot`.
3. Configure the plugin (confirm for yourself that the onion address is currently alive):

```json
{
  "domains": ["http://loginzlib2vrak5zzpcocc3ouizykn6k5qecgj2tzlnab5wcbqhembyd.onion"],
  "proxy": "socks5://tor:9050"
}
```

An ordinary HTTP/SOCKS proxy is not an onion router and cannot replace Tor.

### Without Docker

Install and start Tor with the SOCKS port listening on localhost only, and set `proxy` to `socks5://127.0.0.1:9050`. With AstrBot in Docker and Tor on the host, Docker Desktop usually uses `socks5://host.docker.internal:9050`; Tor must then listen on an interface Docker can reach, with a firewall limiting the sources.

### Security constraints

- Only HTTP/HTTPS; `.onion` must be a 56-character v3 address and requires a SOCKS proxy.
- Every redirect hop of API calls and downloads is re-checked for SSRF.
- The Tor service publishes no host port and is meant for the Compose network only.

---

## Architecture

| Module | Responsibility |
|--------|----------------|
| `zlib_transport.py` | aiohttp transport: headers, manual same-origin redirects and cookie merging, response classification, bounded reads, guarded download GET (SSRF and peer verification) |
| `zlib_domains.py` | Mirror pool: user / built-in / learned tiers, cooldowns, failover, batch probing |
| `zlib_client.py` | E-API client: account pool login, search, download links, cover fetching, streaming downloads, status and diagnosis |
| `zlib_service.py` | Business layer shared by commands and tools: search, card, index resolution, saving downloads |
| `zlib_cards.py` | Card HTML template and render data (every book field explicitly escaped) |
| `zlib_models.py` | Book, search-result and account models, plus the book-line and detail text handed to the model |
| `zlib_store.py` | Persistent book cache and per-session search memory |
| `zlib_format.py` | Command argument parsing and text formatting |
| `zlib_text.py` | Mojibake repair, filename sanitizing, description cleaning and capping, cover resizing |
| `zlib_security.py` | SSRF checks: private-address detection, second DNS check, onion address rules |
| `zlib_errors.py` | Error categories and which ones trigger failover |
| `zlib_paths.py` | Plugin data directory |
| `tools/` | The three LLM tools |

Downloads are saved under `<data>/plugin_data/zlibrary_assistant/books/` and kept for 7 days.

Downloads run in the background and do not occupy the conversation: the model returns immediately after submitting, so the session stays usable; the plugin sends the finished file itself and states the reason on failure. At most two downloads run at once and the rest queue up; a transfer is abandoned only after 30 seconds without receiving data, so a large file keeps going as long as bytes arrive.

---

## FAQ

### Q1: Every mirror is unavailable, or "DiamWall verification blocked"?

Run `/zlib diag` first. The official main domains require browser verification that scripts cannot pass; whether a mirror is reachable depends on your egress IP, and switching proxy node often helps. You can also add mirrors you have verified yourself under `domains`.

### Q2: "Rate limited"?

The site applies risk control to request frequency and shared egress IPs. Switch proxy node or slow down; the affected mirror cools down automatically and other mirrors take over meanwhile.

### Q3: Download says "quota exhausted" or "no account configured"?

Free accounts have a limited number of downloads per day (see the account status). Wait for the daily reset or add more accounts under `accounts`.

### Q4: Email/password login fails?

The login endpoint is often rejected on its own by risk control. Use `remix_userid` + `remix_userkey` instead (see [Minimal configuration](#minimal-configuration)).

### Q5: Chinese text in the result image appears as boxes?

AstrBot's text-to-image prefers cloud rendering and falls back to local rendering, which needs a system CJK font that the official image does not ship. Fix (any one):

1. Add to the Dockerfile: `RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk`
2. In a running container: `docker exec -it <container> bash -c "apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk"`
3. Copy any CJK font to `data/font.ttf`: `docker cp msyh.ttc <container>:/AstrBot/data/font.ttf`

### Q6: Slow searches or placeholder covers in Docker?

A container is its own network: `http://127.0.0.1:port` points at the container itself. Set `proxy` to an address the container can reach: `http://host.docker.internal:port` on Docker Desktop, or the host's LAN IP on Linux with "allow LAN" enabled in the proxy software; with authentication use `http://user:pass@IP:port`. If cloud text-to-image is unreachable, rendering has a 25-second timeout and falls back to a text list; if time is still tight, raise `agent_runner.config.misc.tool_call_timeout`.

## Changelog

> **[Read the full changelog →](CHANGELOG.md)**

## Support and credits

If this plugin helps you, a Star is welcome. Please file questions and suggestions as an [Issue](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/issues) or [Pull Request](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/pulls).

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) open-source chatbot framework

## License and author

Released under the **MIT License**.

[@OMSociety](https://github.com/OMSociety)
