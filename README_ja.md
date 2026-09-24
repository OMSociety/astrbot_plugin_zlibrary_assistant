<p align="center"><a href="README.md">中文</a> · <a href="README_en.md">English</a> · <a href="README_ru.md">Русский</a> · <strong>日本語</strong></p>

<div align="center">

<img src="https://raw.githubusercontent.com/OMSociety/astrbot_plugin_zlibrary_assistant/main/logo.png" width="120" alt="ZLibrary Assistant Logo" />

# Zlibrary アシスタント

**Z-Library 書籍検索・ダウンロードアシスタント** —— 書籍検索 · ワンクリックダウンロード · アカウントプールローテーション · HTML カード結果 · クォータ管理

[![Version](https://img.shields.io/badge/version-1.1.0-blue.svg)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant)
[![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A5v4-green.svg)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/OMSociety/astrbot_plugin_zlibrary_assistant)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/stargazers)
[![Issues](https://img.shields.io/github/issues/OMSociety/astrbot_plugin_zlibrary_assistant)](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/issues)

</div>

> 本プロジェクトは AI によって作成されました

---

## 主な特徴

| 特徴 | 説明 |
|------|------|
| **書籍検索** | キーワードで Z-Library（3300 万冊以上）を検索し、HTML カード画像（表紙/タイトル/著者/形式/サイズ）+ id 付きテキストリストを返します |
| **ワンクリックダウンロード** | ひとこと言えばダウンロードされ、ファイルは自動で会話に送信されます。pdf / epub / mobi などの形式に対応 |
| **アカウントプールローテーション** | 複数アカウントで 1 日あたりのダウンロードクォータを共有し、残りクォータが最も多いアカウントを自動選択。使い切った場合はわかりやすいメッセージでお断りします |
| **2 種類の認証情報方式** | `remix_userid+remix_userkey`（login エンドポイントのリスク制御を回避）または `email+password`（remix 認証情報を自動取得） |
| **HTML カード結果** | 検索結果は統一されたカード画像にレンダリングされ、表紙の読み込みに失敗すると自動でグラデーション代替画像にフォールバックするため、カードが空白になることはありません |
| **エラー分類** | IP 制限 / ドメイン無効 / ログイン無効 / クォータ枯渇 / ネットワーク異常をすべて分類して処理し、わかりやすいメッセージを表示します。プラグインがクラッシュすることはありません |

---

## 機能概要

### 書籍検索
チャットで探したい本をそのまま伝えると、LLM が自動で検索ツールを呼び出し、**HTML カード画像**（表紙/タイトル/著者/形式/サイズ）と id 付きテキストリストを返します：

```
ユーザー: マルクスの『資本論』を探して
🤖 → zlib_search_books(query=資本論)
    「資本論」の検索で N 件ヒット、カード画像を送信しました ✅
```

### 書籍ダウンロード
ひとこと言えばダウンロードされ、ファイルは自動で会話に送信されます：

```
ユーザー: 1 冊目をダウンロードして
🤖 → zlib_download_book(book_id=<検索結果の id>)
    ダウンロード完了 ✅ PDF ファイルを送信しました。アカウントの残りクォータ: 9 回
```

### アカウントプールおよびクォータ管理
- 複数の Z-Library アカウントを設定でき、**アカウントごとに 1 日あたりのダウンロードクォータが独立**しています（無料アカウントで約 10 回/日）
- ダウンロード時は**今日の残りクォータが最も多い**アカウントへ自動でローテーションし、すべて使い切った場合はメッセージとともにお断りします
- 2 種類の認証情報方式に対応：`email+password` または `remix_userid+remix_userkey`

### HTML カード結果
検索結果は統一されたカード画像（Z-Library の表紙 + メタ情報バッジ）にレンダリングされ、表紙の読み込みに失敗すると自動でグラデーション代替画像にフォールバックするため、カードが空白になることはありません。

### エラー分類
IP 制限 / ドメイン無効 / ログイン無効 / クォータ枯渇 / ネットワーク異常をすべて分類して処理し、わかりやすいメッセージへ変換します。単発のエラーでプラグインがクラッシュすることはありません。

---

## クイックスタート

### ステップ 1：インストール

AstrBot WebUI → プラグインマーケット → GitHub から `astrbot_plugin_zlibrary_assistant` をインストール

### ステップ 2：最小設定（検索およびダウンロードを動かす）

WebUI のプラグイン設定ページで `accounts` にアカウントを 1 つ追加するだけです（remix 方式を推奨）：

1. プラグイン設定ページを開く → `accounts` でアカウント項目を追加
2. **推奨方式**：`remix_userid` + `remix_userkey` を入力（ブラウザで z-library.sk にログイン後、F12 → Cookie からコピー。長期間有効で、login エンドポイントのリスク制御を受けません）
3. または `email` + `password` を入力（ログイン後、remix 認証情報を自動取得）
4. 複数のアカウントが必要な場合は、項目を追加していきます（アカウントプールが自動ローテーション）

保存後に AstrBot を再起動すれば、会話内で直接書籍を検索・ダウンロードできます。

> **ヒント：**中国大陸のサーバーでは `proxy` にプロキシ（例：`http://127.0.0.1:7897`）を指定する必要があります。海外サーバーでは空欄で構いません。安定した onion アクセスを使いたい場合は [Tor/onion 導入](#toronion-導入) を参照してください。

### 依存関係のインストール
プラグインは `aiohttp` + `aiofiles` + `aiohttp-socks` + `Pillow` に依存します。AstrBot がプラグインのインストール時に自動で処理するため、追加のインストールは不要です。

---

## Tor/onion 導入

プラグインはオプションで Tor 経由により Z-Library の onion E-API にアクセスでき、従来の平文ドメインおよび HTTP プロキシ設定との後方互換性は保たれます。提供する LLM ツールは従来どおり 3 つです：

- `zlib_search_books`：書籍を検索
- `zlib_download_book`：検索結果の id でダウンロード
- `zlib_get_status`：アカウントとクォータを確認

ネットワークリクエストは SOCKS5 経由で Tor に入り、`.onion` の DNS 解決は Tor 側で行われます。プラグインは任意の URL を閲覧するツールを提供しないため、プロンプトインジェクションによって Bot がオープンプロキシ化されることはありません。

### Docker での導入

1. 通常どおり `astrbot_plugin_zlibrary_assistant` をインストールします。
2. 次の `tor` サービスを AstrBot の `docker-compose.yml` にマージします（9050 を公衆網に公開しないでください）：

```yaml
services:
  tor:
    build: ./data/plugins/astrbot_plugin_zlibrary_assistant/docker/tor
    restart: unless-stopped
    # Tor が既存のプロキシを経由して公衆網に接続する必要がある場合は、実際の種類に応じて有効化：
    # environment:
    #   - TOR_UPSTREAM_PROXY=http://proxy-host:port
```

3. AstrBot と `tor` が同じ Compose ネットワークにあることを確認し、再ビルドして起動します：

```bash
docker compose up -d --build tor astrbot
```

4. AstrBot のプラグイン設定で次のように入力します：

```json
{
  "domain": "http://loginzlib2vrak5zzpcocc3ouizykn6k5qecgj2tzlnab5wcbqhembyd.onion",
  "proxy": "socks5://tor:9050",
  "max_download_mb": 80,
  "search_limit": 5,
  "accounts": []
}
```

通常の HTTP/SOCKS プロキシは onion ルーターではないため、Tor の代わりにはなりません。プラグインは常に `socks5://tor:9050` に接続します。Tor 自体に上流プロキシが必要な場合は、既存サービスの実際のプロトコルに応じて `TOR_UPSTREAM_PROXY=http://proxy-host:port` または `TOR_UPSTREAM_PROXY=socks5://proxy-host:port` を設定します。上流サービスの名前・アドレス・ポートは実際の構成に合わせて入力してください。

アカウント設定は従来のプラグインと同じです。ログインエンドポイントへの頻繁な呼び出しを避けるため、`remix_userid` + `remix_userkey` を推奨します。

### Docker を使わない導入

先に Tor をインストール・起動し、SOCKS ポートをローカルのみでリッスンさせてから、`proxy` に `socks5://127.0.0.1:9050` を設定します。AstrBot が Docker 内、Tor がホスト上にある場合、Docker Desktop では通常 `socks5://host.docker.internal:9050` を使用します。この場合、Tor を Docker から到達可能なインターフェースでリッスンさせ、ファイアウォールで接続元を制限してください。

### セキュリティ上の制約

- 受け付けるのは HTTP/HTTPS のみ。`.onion` は 56 文字の v3 アドレスであること。
- `.onion` へのリクエストは SOCKS プロキシが必須。ローカルでの DNS 解決は禁止。
- API が返すリダイレクトは毎ホップで SSRF 検証をやり直します。
- 1 ファイルあたり既定 80 MiB まで。異常なレスポンスによるメモリ枯渇を防ぎます。
- Tor サービスはホストのポートを公開せず、Compose 内部ネットワーク専用です。

### 検証

```bash
python -m pytest -q
```

実際の接続性は、Tor が bootstrap を完了しているか、対象の onion がオンラインか、アカウントの資格情報が有効かにも依存します。

---

## 設定項目の説明

### アカウント設定

| 設定項目 | 型 | デフォルト | 説明 |
|--------|------|------|------|
| `accounts` | template_list | `[]` | Z-Library アカウントプール。1 項目 = 1 アカウント。`remix_userid`+`remix_userkey`（ペア）または `email`+`password` を推奨 |
| `accounts[].name` | string | `account` | アカウントのメモ名（ログでの識別に使用） |
| `accounts[].email` | string | `""` | アカウントのメールアドレス（password とペア） |
| `accounts[].password` | string | `""` | アカウントのパスワード |
| `accounts[].remix_userid` | string | `""` | remix_userid（remix_userkey とペア、推奨） |
| `accounts[].remix_userkey` | string | `""` | remix_userkey（推奨） |

### 接続設定

| 設定項目 | 型 | デフォルト | 説明 |
|--------|------|------|------|
| `domain` | string | `z-library.sk` | Z-Library E-API のドメインまたは完全なベース URL。通常のドメインでは既定で HTTPS を使用。`http://...onion` も指定可能 |
| `proxy` | string | `""` | HTTP または SOCKS プロキシ（任意）。onion アドレスには必ず Tor SOCKS を使用（例：`socks5://tor:9050`） |
| `max_download_mb` | int | `80` | 1 回のダウンロードで取得できるファイルの最大サイズ（MiB、範囲 1-512） |

### 検索設定

| 設定項目 | 型 | デフォルト | 説明 |
|--------|------|------|------|
| `search_limit` | int | `5` | 検索の既定の最大返却件数（LLM が指定しない場合に使用、範囲 1-8） |

### クイック設定テンプレート

WebUI の設定パネルで入力するか、以下の構造を参考にしてください（`data/config/astrbot_plugin_zlibrary_assistant_config.json`）：

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

## LLM 呼び出し可能ツール

プラグインは 3 つの LLM ツールを登録しており、モデルが呼び出しタイミングを自動判断します。ユーザーは自然な言葉で要望を伝えるだけです：

```
ユーザー: マルクスの『資本論』を探して
🤖 → zlib_search_books(query=資本論)
    「資本論」の検索で N 件ヒット、カード画像を送信しました ✅

ユーザー: 1 冊目をダウンロードして
🤖 → zlib_download_book(book_id=123456)
    ダウンロード完了 ✅ PDF ファイルを送信しました。アカウントの残りクォータ: 9 回

ユーザー: 今日あと何冊ダウンロードできますか？
🤖 → zlib_get_status()
    Z-Library アカウントプール状態: account1 ✅ 正常、今日のダウンロード 1/10 回
```

### zlib_search_books
キーワードで Z-Library の書籍ライブラリを検索し、カード画像 + id 付きテキストリストを返します。検索はダウンロードクォータを**消費しません**。

| パラメータ | 型 | 説明 |
|------|------|------|
| `query` | string | **必須**。検索キーワード（書名/著者/テーマ、中国語・英語に対応） |
| `limit` | int? | 最大返却件数（未指定の場合は設定の `search_limit` を使用、最大 8） |
| `language` | string? | 言語フィルタ（例：`zh` / `en` / `fr`） |
| `extension` | string? | 形式フィルタ（例：`pdf` / `epub` / `mobi`） |

### zlib_download_book
検索結果の id で書籍をダウンロードしてローカルに保存します。**ユーザーが明示的にダウンロードを求めた場合のみ呼び出し**、アカウントの 1 日あたりクォータを消費します。

| パラメータ | 型 | 説明 |
|------|------|------|
| `book_id` | int | **必須**。書籍の id（zlib_search_books が返す番号付きリストの番号） |

### zlib_get_status
アカウントプール内の各アカウントのログイン状態と今日の残りダウンロードクォータを照会します。

| パラメータ | 型 | 説明 |
|------|------|------|
| （引数なし） | - | - |

---

## アーキテクチャ

### eapi クライアント（zlib_client.py）
Z-Library Android クライアントの内部インターフェース（非公式 E-API）の非同期ラッパー：
- aiohttp ベースで、AstrBot の開発規約に準拠（非同期、requests 禁止）
- アカウントプール：アカウントごとに独立したクォータ状態を保持し、ダウンロード時は残りクォータが最も多いアカウントを自動選択
- エラー分類：`rate_limited` / `auth_failed` / `quota_exhausted` / `domain_invalid` / `network_error` / `api_error`
- 書籍キャッシュはディスクに永続化され、AstrBot 再起動後も id だけで直接ダウンロード可能
- mojibake 修正：Z-Library が返す二重エンコードされたテキストを自動復元
- ドメインの自動正規化（スキーム未指定の場合は通常ドメインに `https://` を、onion には `http://` を補完し、パス付きのアドレスは拒否します）。ダウンロードファイル名は、Windows のファイル名に使えない文字を自動的に除去します

### ツール層（tools/）
3 つの `FunctionTool` が `add_llm_tools` で登録され、LLM が会話の中で自動認識して呼び出します：
- `search_tool.py` — 検索 + HTML カードレンダリング（レンダリング失敗時は自動でプレーンテキストにフォールバック）
- `download_tool.py` — id で `<data>/zlibrary_assistant/books/` へダウンロードし、絶対パスを返す
- `status_tool.py` — アカウントプールの状態照会

### カードレンダリング（templates.py）
HTML + Jinja2 テンプレートで、AstrBot 内蔵のテキスト画像化（`html_renderer`）を使用：
- CSS フォントスタックにより、クラウドレンダリングで中国語フォントを自動選択し、ローカルフォントに依存しません
- 表紙はプラグイン側でダウンロードして **base64 埋め込み**（クラウドレンダラーが Z-Library 外部リンクにアクセスする必要なし）。ダウンロード失敗時は自動でグラデーション代替画像を表示
- タイトルは 2 行で切り詰め、著者は 1 行で省略。長いリストでもレイアウトが安定します

---

## よくある質問

### Q1：Docker デプロイ時に検索結果画像の中国語が豆腐（□）/文字化けになる？

**原因**：AstrBot の「テキスト画像化」には 2 つの経路があります。クラウドレンダリング（既定、フォントはクラウド側にあり、ローカルフォント不要）とローカルレンダリング（クラウド失敗時の自動フォールバック、Pillow でレンダリング）です。ローカルレンダリングにはシステムの中国語フォントが必要ですが、AstrBot 公式イメージ（`python:3.12-slim`）には**中国語フォントが一切プリインストールされておらず**、Pillow がフォントを見つけられないと内蔵ビットマップフォントを使うため、中国語がすべて豆腐になります。

**解決策**（いずれか 1 つ）：
1. Dockerfile に 1 行追加（推奨、イメージ再ビルドで有効化）：
   ```dockerfile
   RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk
   ```
2. 起動中のコンテナに直接インストール：
   ```bash
   docker exec -it <コンテナ名> bash -c "apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk"
   ```
3. パッケージをインストールせず、任意の中国語フォントファイル（例：Microsoft YaHei の `msyh.ttc`）を AstrBot の `data/` ディレクトリに `font.ttf` という名前で置く：
   ```bash
   docker cp msyh.ttc <コンテナ名>:/AstrBot/data/font.ttf
   ```

### Q2：「Z-Library が現在の IP を制限しています」と表示される？

Z-Library はアクセス頻度に厳しいリスク制御（エラーコード `#ipd3`）を敷いており、共有プロキシ IP（いわゆる「空港」ノードなど）はマークされやすいです。解決策：
- プロキシノードを変更する（Clash/V2Ray でノードを切り替え）
- または短時間のリクエスト頻度を下げる
- プラグインはこのエラーを分類してわかりやすいメッセージを表示し、クラッシュせず、クールダウン後に自動復旧します

### Q3：「現在のドメインは無効の可能性があります」と表示される？

Z-Library のドメインは差押え/変更が頻繁です（実測では常用ドメインの約半分が無効）。解決策：プラグイン設定の `domain` を現在有効なドメイン（例：`z-library.sk`、**`https://` 接頭辞は不要**）に変更します。有効性は事前に `https://<ドメイン>/eapi/info` にアクセスして確認できます（JSON が返れば有効）。

### Q4：ダウンロード時に「アカウントプールのクォータを使い切りました」と表示される？

Z-Library の無料アカウントには 1 日あたりのダウンロード回数制限（約 10 回/日）があります。解決策：
- 翌日のクォータリセットを待つ
- `accounts` 設定にアカウントを追加する（アカウントプールが残りクォータ最大のアカウントへ自動ローテーション）

### Q5：ログインが「IP 制限」（#ipd3）ばかり表示するのに、ウェブサイトは開ける？

**原因**：Z-Library は **login エンドポイント**に独立したリスク制御（読み取り専用エンドポイントより厳しい）を設けており、「空港」の共有 IP はマークされやすいです。`/eapi/info` などの読み取り専用インターフェースはアクセスできるままですが、`email+password` ログインは拒否されます。

**解決策**：**`remix_userid` + `remix_userkey`** 方式に切り替えます（login エンドポイントを通らず、GET 検証のみのため、このリスク制御をほとんど受けません）：
1. Z-Library に一度でも正常にログインした後、個人ページ/クライアントでこの 2 つの値を取得
2. プラグイン設定の `accounts` に `remix_userid` および `remix_userkey` を入力（email/password は空欄）
3. プラグインが GET `/eapi/user/profile` で検証し、検索/ダウンロードが通常どおり使えます

> **ヒント：**`remix_userkey` は長期間有効で、一度設定すれば継続して使えます。

### Q6：ダウンロードは完了したのにファイルが送信されず、"Sandbox runtime is disabled by configuration" と表示される？

**原因**：AstrBot の `send_message_to_user` によるローカルファイル送信は **Computer Use ローカルランタイム**（`computer_use_runtime`）に依存しますが、AstrBot の既定値は `none` のため、ローカルファイルがサンドボックスにブロックされます。

**解決策**（WebUI または設定ファイルのどちらか）：
1. **WebUI**：`設定（Config）→ サービスプロバイダー設定 → computer_use_runtime` を `local` に変更（`computer_use_require_admin` を `false` にしても可）
2. **設定ファイル**：`data/config/astrbot_config.json` を編集（存在しなければ作成）：
   ```json
   {
     "provider_settings": {
       "computer_use_runtime": "local",
       "computer_use_require_admin": false
     }
   }
   ```
   保存後に AstrBot を再起動します。

### Q7：どの依存関係を設定すればよい？

プラグインは `aiohttp` + `aiofiles` + `aiohttp-socks` + `Pillow` に依存し、AstrBot がプラグインのインストール時に自動処理します。テキスト画像化は AstrBot 内蔵機能を使用するため、追加インストールは不要です。

### Q8：Docker デプロイ時に検索が遅い / カードの表紙がすべて代替画像 / ツールが timeout を報告する？

**原因**：Docker コンテナは独立したネットワーク環境のため、設定の `http://127.0.0.1:7897` は**コンテナ自身**（あなたのプロキシは中にない）を指し、表紙ダウンロードがすべて失敗（代替画像を表示）します。さらに、クラウドのテキスト画像化にコンテナ内から接続できない場合、レンダリングがツール全体のタイムアウト（AstrBot 既定の `tool_call_timeout` 120 秒）を使い切ります。

**解決策**：
1. **コンテナから到達できるプロキシアドレスを指定**：形式は `http://IP:ポート`。プロキシに認証が必要な場合は `http://ユーザー名:パスワード@IP:ポート`（例：`http://user:pass@IP:port`）。プラグインは aiohttp ベースで、`Proxy-Authorization` 認証ヘッダーを自動送信します。Windows/Mac の Docker Desktop では `http://host.docker.internal:ポート` を使用でき、Linux ではホストの LAN IP を使い、プロキシソフトで「LAN 接続を許可」を有効にしてください
2. クラウドのテキスト画像化に届かない場合：プラグインには**25 秒のレンダリングタイムアウト保護**が組み込まれており、レンダリング失敗時は自動でプレーンテキストの書籍リストにフォールバックします（ツール全体はエラーになりません）。許容できるなら対処不要です
3. それでも時間が足りない場合：AstrBot 設定の `agent_runner.config.misc.tool_call_timeout` を大きくします（例：`240`）

## 更新履歴

> **[更新履歴の全文を見る →](CHANGELOG.md)**

## 応援と謝辞

このプラグインが役に立ったら、Star をぜひお願いします。問題や提案があれば [Issue](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/issues) または [Pull Request](https://github.com/OMSociety/astrbot_plugin_zlibrary_assistant/pulls) をお寄せください。

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) オープンソースチャットボットフレームワーク

## ライセンスと作者

本プロジェクトは **MIT License** で公開しています。

[@OMSociety](https://github.com/OMSociety)
