"""搜索结果卡片：HTML/Jinja2 模板与渲染数据。

卡片经 AstrBot 文转图渲染成一张 JPEG，视觉为构成主义风格（红、黑、米白三色，
粗体无衬线、斜向色块、硬投影）：
- 字体用 CSS 字体栈，由渲染端挑选可用中文字体；
- 封面由插件端抓取后以 base64 内嵌（渲染端访问不了封面 CDN），抓取失败的书
  使用内置占位，卡片不留空；
- 渲染端是否启用 Jinja2 autoescape 取决于端点实现（实测官方端点未启用），
  所有书目字段因此在模板里显式经 `|e` 转义：autoescape 开启时不会二次转义，
  关闭时可阻止站点数据注入 HTML。
"""

from __future__ import annotations

from .zlib_models import SearchResult
from .zlib_text import flatten_field

# 渲染参数：AstrBot 默认 JPEG 质量 40，文字边缘发糊；90 在体积与清晰度间折中
RENDER_OPTIONS = {"full_page": True, "type": "jpeg", "quality": 90}

# 单张卡片最多展示的书数；超出会使图片过长而被聊天平台压缩
MAX_CARD_BOOKS = 10

# 格式 -> 标签配色（背景, 文字），限定在红/黑/赭三色内
_FORMAT_COLORS = {
    "pdf": ("#d42a1e", "#f3ead7"),
    "epub": ("#141414", "#f3ead7"),
    "mobi": ("#c8901d", "#141414"),
    "azw3": ("#c8901d", "#141414"),
}
_DEFAULT_FORMAT_COLOR = ("#f3ead7", "#141414")

SEARCH_CARD_TMPL = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { background: #f3ead7; }
  body {
    width: 800px;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    background: #f3ead7;
    font-family: "Noto Sans SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    color: #141414;
    overflow: hidden;
  }
  .head {
    background: #141414;
    padding: 26px 36px 24px;
    border-bottom: 12px solid #d42a1e;
  }
  .head .kicker {
    font-size: 14px; font-weight: 900; letter-spacing: 6px;
    color: #d42a1e;
  }
  .head .q {
    margin-top: 9px;
    font-size: 34px; font-weight: 900; line-height: 1.22;
    color: #f3ead7; letter-spacing: 1px;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; word-break: break-all;
  }
  .meta {
    position: relative;
    align-self: flex-start;
    margin: 22px 0 0 36px;
    display: inline-block;
    background: #f3ead7;
    border: 4px solid #141414;
    padding: 6px 16px;
    font-size: 16px; font-weight: 900; letter-spacing: 1px;
    box-shadow: 6px 6px 0 #d42a1e;
  }
  .list { padding: 26px 28px 6px; flex: 1 0 auto; }
  .item {
    position: relative;
    display: flex;
    align-items: stretch;
    background: #fbf6ea;
    border: 4px solid #141414;
    margin-bottom: 20px;
    box-shadow: 8px 8px 0 #141414;
  }
  .no {
    width: 74px; flex-shrink: 0;
    background: #d42a1e;
    color: #f3ead7;
    display: flex; align-items: flex-start; justify-content: center;
    padding-top: 12px;
    font-size: 40px; font-weight: 900; line-height: 1;
    border-right: 4px solid #141414;
  }
  .item:nth-child(even) .no { background: #141414; }
  .cover {
    position: relative;
    width: 96px; height: 136px;
    flex-shrink: 0;
    margin: 14px 0 14px 16px;
    border: 3px solid #141414;
    background: #141414;
    overflow: hidden;
  }
  .cover .ph {
    position: absolute; inset: 0;
    background:
      linear-gradient(135deg, transparent 0 46%, #d42a1e 46% 62%, transparent 62%),
      #141414;
  }
  .cover .ph span {
    position: absolute; left: 10px; bottom: 8px;
    font-size: 30px; font-weight: 900; color: #f3ead7;
  }
  .cover img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
  .info {
    flex: 1; min-width: 0;
    padding: 14px 18px 14px 18px;
    display: flex; flex-direction: column;
  }
  .title {
    font-size: 21px; font-weight: 900; line-height: 1.3;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; word-break: break-all;
  }
  .rule { width: 56px; height: 5px; background: #d42a1e; margin: 9px 0 8px; }
  .author {
    font-size: 15px; font-weight: 700;
    overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
  }
  .pub {
    margin-top: 3px; font-size: 13px; color: #5a5348;
    overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
  }
  .tags { margin-top: auto; padding-top: 10px; white-space: nowrap; }
  .tag {
    display: inline-block; margin-right: 8px;
    padding: 3px 10px;
    border: 2px solid #141414;
    font-size: 13px; font-weight: 800; letter-spacing: .5px;
    background: #f3ead7; color: #141414;
  }
  .tag.fmt { letter-spacing: 2px; }
  .foot {
    position: relative;
    margin-top: 10px;
    background: #141414;
    color: #f3ead7;
    padding: 18px 36px 18px 60px;
    font-size: 15px; font-weight: 700; line-height: 1.5;
    overflow: hidden;
  }  .foot::before {
    content: ""; position: absolute; left: 0; top: 0; bottom: 0;
    width: 30px; background: #d42a1e;
  }
</style>
</head>
<body>
  <div class="head">
    <div class="kicker">Z-LIBRARY · 搜索</div>
    <div class="q">{{ query | e }}</div>
  </div>
  <div class="meta">{{ meta | e }}</div>
  <div class="list">
  {% for b in books %}
  <div class="item">
    <div class="no">{{ b.no | e }}</div>
    <div class="cover">
      <div class="ph"><span>书</span></div>
      {% if b.cover %}<img src="{{ b.cover | e }}" onerror="this.style.display='none'">{% endif %}
    </div>
    <div class="info">
      <div class="title">{{ b.title | e }}</div>
      <div class="rule"></div>
      <div class="author">{{ b.author | e }}</div>
      {% if b.publisher %}<div class="pub">{{ b.publisher | e }}</div>{% endif %}
      <div class="tags">
        {% if b.extension %}<span class="tag fmt" style="background:{{ b.fmt_bg }};color:{{ b.fmt_fg }}">{{ b.extension | e }}</span>{% endif %}
        {% if b.size %}<span class="tag">{{ b.size | e }}</span>{% endif %}
        {% if b.language %}<span class="tag">{{ b.language | e }}</span>{% endif %}
        {% if b.year %}<span class="tag">{{ b.year | e }}</span>{% endif %}
      </div>
    </div>
  </div>
  {% endfor %}
  </div>
  <div class="foot">{{ footer | e }}</div>
</body>
</html>
"""


def build_card_data(
    result: SearchResult,
    covers: dict[int, str],
    prefix: str,
    quota_hint: str = "",
) -> dict:
    """构造模板数据。

    covers: book id -> data URI（缺失表示用占位）；
    prefix: 指令前缀，用于页脚提示（例如 "/zlib"）；
    quota_hint: 账号额度提示，空串表示不提额度。
    """
    books = []
    for i, b in enumerate(result.books[:MAX_CARD_BOOKS], start=1):
        fmt_bg, fmt_fg = _FORMAT_COLORS.get(b.extension, _DEFAULT_FORMAT_COLOR)
        books.append(
            {
                "no": i,
                "title": flatten_field(b.title or "未知标题", 200),
                "author": flatten_field(b.author or "未知作者", 80),
                "publisher": flatten_field(b.publisher, 60),
                "extension": b.extension.upper(),
                "size": flatten_field(b.size, 20),
                "language": flatten_field(b.language, 20),
                "year": flatten_field(b.year, 8),
                "cover": covers.get(b.id, ""),
                "fmt_bg": fmt_bg,
                "fmt_fg": fmt_fg,
            }
        )
    meta = f"第 {result.page}/{result.total_pages} 页 · 共 {result.total_items} 条"
    hints = [f"回复 {prefix} 详情 序号 看简介", f"{prefix} 下载 序号 取文件"]
    if result.has_next:
        hints.append(f"{prefix} 下一页")
    if quota_hint:
        hints.append(quota_hint)
    return {
        "query": flatten_field(result.query, 60),
        "meta": meta,
        "books": books,
        "footer": "，".join(hints),
    }
