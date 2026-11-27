<p align="center"><a href="README.md">中文</a> · <a href="README_en.md">English</a> · <a href="README_ru.md">Русский</a> · <strong>日本語</strong></p>

<div align="center">

<h1><img src="https://raw.githubusercontent.com/OMSociety/astrbot_plugin_zlibrary_assistant/main/logo.png" width="192" alt="Zlibrary Assistant"><br>Zlibrary Assistant</h1>

**Z-Library 書籍検索・ダウンロードアシスタント** —— ミラー自動フェイルオーバー · `/zlib` コマンド · カード画像表示 · アカウントプールの割当管理

[![Version](https://img.shields.io/github/v/tag/OMSociety/astrbot_plugin_zlibrary_assistant?label=version&color=blue)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/releases)
[![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A5v4-green.svg)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/OMSociety/astrbot_plugin_zlibrary_assistant)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/stargazers)
[![Issues](https://img.shields.io/github/issues/OMSociety/astrbot_plugin_zlibrary_assistant)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/issues)

</div>

> 本プロジェクトは AI によって作成されています。

Zlibrary Assistant は AstrBot に Z-Library の書籍検索とダウンロードを提供します。複数のミラードメインを内蔵して自動でフェイルオーバーし、`/zlib` コマンドと LLM ツールのどちらからでも使えます。検索結果はカード画像で表示され、ダウンロードはアカウントプールが残り枠の多い順に切り替えて行います。

---

## クイックスタート

AstrBot WebUI のプラグインマーケットで「Zlibrary助手」を検索してインストールします。

### 最小構成

1. 検索のみ：設定は不要です（サーバーからミラーに直接届かない場合は `proxy` を設定）。
2. ダウンロードする：`accounts` にアカウントを 1 つ追加します。`remix_userid` + `remix_userkey` を推奨します。ブラウザで Z-Library にログインし、F12 → Application → Cookie から両方の値をコピーしてください。長期間有効で、リスク制御に拒否されやすいログインエンドポイントを使いません。`email` + `password` も使えます。
3. 保存してプラグインを再読み込みし、ボットに送信します。

```
/zlib search 資本論 -l zh
/zlib download 1
```

> **注意**：公式メインドメイン（`z-library.sk` など）はブラウザ検証があり、スクリプトでは通過できません。そのため内蔵ミラーを使用します。`/zlib diag` で現在到達できるミラーを確認できます。

---

## コマンド

コマンドグループは `/zlib`、別名 `/zl`。サブコマンドは中国語名が正式名で、英語の別名も使えます。

| コマンド | 説明 |
|----------|------|
| `/zlib 搜索 キーワード [-l 言語] [-e 形式] [-n 件数]` | 検索。例：`/zlib 搜索 資本論 -l zh -e pdf`。別名 `search`、`s`、`搜` |
| `/zlib 下一页` / `/zlib 上一页` | 直近の検索結果をページ送り。別名 `next`/`n`、`prev`/`p` |
| `/zlib 下载 番号または id` | 番号は現在のセッションの直近の結果を指します。ページ内の件数を超える数字は書籍 id として扱います。別名 `download`、`dl`、`下` |
| `/zlib 状态` | アカウントのログイン状態と本日の残り枠（管理者のみ）。別名 `status` |
| `/zlib 诊断` | 各ミラーの到達性と遅延を調べ、アカウントのログインを確認（管理者のみ）。別名 `diag` |
| `/zlib 帮助` | コマンドの説明。別名 `help` |

言語オプションは `zh`、`en`、`chinese`、`english` などを受け付けます。検索はダウンロード枠を消費しません。`download_admin_only` をオンにすると、ダウンロードは管理者のみになります。

---

## LLM ツール

| ツール | パラメータ | 説明 |
|--------|------------|------|
| `zlib_search_books` | `query`（必須）、`limit`、`page`、`language`、`extension` | 検索。カード画像のパスと id 付きテキスト一覧を返します |
| `zlib_download_book` | `book_id`（必須） | 検索結果の id でダウンロード。枠を消費するため、ユーザーが明示的に求めた場合のみ呼び出します |
| `zlib_get_status` | なし | アカウントプールの状態。管理者セッションのみ |

```
ユーザー: マルクスの『資本論』を探して。中国語の PDF で
モデルの呼び出し: zlib_search_books(query="資本論", language="zh", extension="pdf")
ユーザー: 1 冊目をダウンロードして
モデルの呼び出し: zlib_download_book(book_id=<検索結果の id>)
```

書誌フィールドは、ツール結果に入る前に区切りマーカーを除去し長さを制限します。題名に紛れた指示によるプロンプトインジェクションのリスクを下げるためです。ツールがファイルパスを返したら、モデルが `send_message_to_user`（`type=file`）で送信します。

---

## ミラーとフェイルオーバー

- 内蔵ミラー：`z-lib.by`、`z-library.website`、`z-lib.gd`、`z-lib.gl`、`article.sk`、`articles.sk`、`z-library.qa`、`zlib.bz`。
- ミラーの E-API へのリクエストには `source: android` ヘッダーを付けます。ネットワークエラー、証明書エラー、ブラウザ検証によるブロック、Cloudflare によるブロック、レート制限でフェイルオーバーし、そのミラーはエラーの種類に応じて冷却されます。
- `domains` のドメインが最優先、次に内蔵ミラー。ミラーから学習したドメインは最後で、アカウント認証情報を含まないリクエストにのみ使われます。
- ミラーに届くかどうかは、利用している出口ネットワークに依存します。`/zlib 诊断` の結果で判断してください。

---

## 設定項目

### アカウント設定

| 設定 | 型 | 既定値 | 説明 |
|------|----|--------|------|
| `accounts` | template_list | `[]` | アカウントプール（1 項目に 1 アカウント）。remix とメール/パスワードを両方入力した場合は remix が優先されます |
| `accounts[].name` | string | `account` | ログと状態表示に使うアカウント名 |
| `accounts[].remix_userid` | string | `""` | remix_userid（推奨。remix_userkey とセット） |
| `accounts[].remix_userkey` | string | `""` | remix_userkey（推奨） |
| `accounts[].email` | string | `""` | アカウントのメール（代替。パスワードとセット） |
| `accounts[].password` | string | `""` | アカウントのパスワード（代替） |

### 接続設定

| 設定 | 型 | 既定値 | 説明 |
|------|----|--------|------|
| `domains` | list | `[]` | 追加のミラードメイン（ホスト名のみ）。onion には SOCKS プロキシが必要です |
| `proxy` | string | `""` | HTTP または SOCKS プロキシ（例：`http://127.0.0.1:7890`） |
| `max_download_mb` | int | `80` | 1 ファイルのダウンロード上限（MiB、1-512） |

### 検索とダウンロードの設定

| 設定 | 型 | 既定値 | 説明 |
|------|----|--------|------|
| `search_limit` | int | `5` | 検索結果の既定件数（1-10） |
| `card_enabled` | bool | `true` | 検索結果をカード画像で表示。オフ、またはレンダリング失敗時はテキスト一覧を返します |
| `download_admin_only` | bool | `false` | ダウンロードを管理者のみに限定 |

### クイック設定テンプレート

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

## Tor/onion デプロイ

プラグインは Tor 経由で onion の入口にも接続できます。`.onion` ドメインには SOCKS プロキシが必須で、DNS 解決は Tor 側で行われます。任意 URL の閲覧機能はなく、プロンプトインジェクションされたボットがオープンプロキシになることを防ぎます。

### Docker

1. 次の `tor` サービスを AstrBot の `docker-compose.yml` にマージします（9050 番ポートは公開しないでください）。

```yaml
services:
  tor:
    build: ./data/plugins/astrbot_plugin_zlibrary_assistant/docker/tor
    restart: unless-stopped
    # Tor が既存のプロキシ経由で出る必要がある場合のみ有効化：
    # environment:
    #   - TOR_UPSTREAM_PROXY=http://proxy-host:port
```

2. AstrBot と `tor` を同じ Compose ネットワークに置き、`docker compose up -d --build tor astrbot` を実行します。
3. プラグインを設定します（onion アドレスが現在有効かどうかはご自身で確認してください）。

```json
{
  "domains": ["http://loginzlib2vrak5zzpcocc3ouizykn6k5qecgj2tzlnab5wcbqhembyd.onion"],
  "proxy": "socks5://tor:9050"
}
```

通常の HTTP/SOCKS プロキシは onion ルーターではなく、Tor の代わりにはなりません。

### Docker を使わない場合

Tor をインストールして起動し、SOCKS ポートをローカルのみで待ち受けさせ、`proxy` を `socks5://127.0.0.1:9050` に設定します。AstrBot が Docker、Tor がホストにある場合、Docker Desktop では通常 `socks5://host.docker.internal:9050` を使います。その場合 Tor を Docker から到達できるインターフェースで待ち受けさせ、ファイアウォールで接続元を制限してください。

### セキュリティ上の制約

- HTTP/HTTPS のみ。`.onion` は 56 文字の v3 アドレスで、SOCKS プロキシが必須です。
- API とダウンロードの各リダイレクトで SSRF 検査をやり直します。
- Tor サービスはホストのポートを公開せず、Compose 内部ネットワーク専用です。

---

## 構成

| モジュール | 役割 |
|------------|------|
| `zlib_transport.py` | aiohttp トランスポート：ヘッダー、同一オリジンの手動リダイレクトと Cookie 統合、応答分類、上限付き読み取り、保護されたダウンロード GET（SSRF と接続先検証） |
| `zlib_domains.py` | ミラープール：ユーザー/内蔵/学習の 3 階層、冷却、フェイルオーバー、一括プローブ |
| `zlib_client.py` | E-API クライアント：アカウントプールのログイン、検索、ダウンロードリンク、表紙取得、状態と診断 |
| `zlib_service.py` | コマンドとツールが共有する業務層：検索、カード、番号の解決、ダウンロードの保存 |
| `zlib_cards.py` | カードの HTML テンプレートと描画データ（全書誌フィールドを明示的にエスケープ） |
| `zlib_store.py` | 書誌キャッシュ（永続化）とセッションごとの検索記憶 |
| `zlib_format.py` | コマンド引数の解析とテキスト整形 |
| `tools/` | 3 つの LLM ツール |

ダウンロードしたファイルは `<data>/plugin_data/zlibrary_assistant/books/` に保存され、7 日間保持されます。

---

## FAQ

### Q1：すべてのミラーが使えない、または「DiamWall 検証でブロック」と表示される

まず `/zlib 诊断` を実行してください。公式メインドメインはブラウザ検証があり、スクリプトでは通過できません。ミラーに届くかは出口 IP に依存し、プロキシノードの切り替えが効くことが多いです。ご自身で動作を確認したミラーを `domains` に追加することもできます。

### Q2：「レート制限」と表示される

サイト側がアクセス頻度や共有の出口 IP にリスク制御をかけています。プロキシノードを変えるか、頻度を下げてください。該当ミラーは自動で冷却され、その間は他のミラーが使われます。

### Q3：ダウンロードで「枠を使い切った」「アカウント未設定」と表示される

無料アカウントの 1 日のダウンロード回数には上限があります（アカウント状態を参照）。翌日のリセットを待つか、`accounts` にアカウントを追加してください。

### Q4：メール/パスワードでログインできない

ログインエンドポイントだけがリスク制御で拒否されることがよくあります。`remix_userid` + `remix_userkey` を使ってください（[最小構成](#最小構成)を参照）。

### Q5：結果画像の中国語が四角（豆腐）になる

AstrBot のテキスト画像化はクラウドレンダリングを優先し、失敗するとローカルレンダリングに切り替わります。ローカルレンダリングにはシステムの CJK フォントが必要ですが、公式イメージには含まれていません。次のいずれかで解決します。

1. Dockerfile に追加：`RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk`
2. 実行中のコンテナ：`docker exec -it <コンテナ名> bash -c "apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk"`
3. 任意の CJK フォントを `data/font.ttf` としてコピー：`docker cp msyh.ttc <コンテナ名>:/AstrBot/data/font.ttf`

### Q6：LLM がダウンロードを終えてもファイルを送れない（"Sandbox runtime is disabled by configuration"）

`send_message_to_user` によるローカルファイルの送信は AstrBot の `computer_use_runtime` に依存し、既定は `none` です。WebUI の 設定 → プロバイダー設定 で `computer_use_runtime` を `local` に変更し（必要なら `computer_use_require_admin` を `false` に）、または `data/config/astrbot_config.json` に次を書き込みます。

```json
{"provider_settings": {"computer_use_runtime": "local", "computer_use_require_admin": false}}
```

保存後に AstrBot を再起動してください。`/zlib 下载` コマンドはファイルを直接送信するため影響を受けません。

### Q7：Docker で検索が遅い、表紙がすべてプレースホルダーになる

コンテナは独立したネットワークで、`http://127.0.0.1:ポート` はコンテナ自身を指します。`proxy` にはコンテナから到達できるアドレスを設定してください。Docker Desktop なら `http://host.docker.internal:ポート`、Linux ならホストの LAN IP を使い、プロキシソフトで LAN 接続を許可します。認証が必要なら `http://ユーザー名:パスワード@IP:ポート` と書きます。クラウドのテキスト画像化に届かない場合、レンダリングには 25 秒のタイムアウトがありテキスト一覧に切り替わります。それでも時間が足りなければ `agent_runner.config.misc.tool_call_timeout` を大きくしてください。

## 変更履歴

> **[変更履歴の全文を見る →](CHANGELOG.md)**

## サポートと謝辞

このプラグインが役に立ったら Star をお願いします。質問や提案は [Issue](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/issues) または [Pull Request](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/pulls) でお寄せください。

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) オープンソースのチャットボットフレームワーク

## ライセンスと作者

本プロジェクトは **MIT License** で公開されています。

[@OMSociety](https://github.com/OMSociety)
