"""搜索结果卡片：HTML/Jinja2 模板与渲染数据。

卡片经 AstrBot 文转图渲染成一张 JPEG：
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

# 格式 -> 标签配色（背景, 文字）
_FORMAT_COLORS = {
    "pdf": ("#fee2e2", "#b91c1c"),
    "epub": ("#dcfce7", "#15803d"),
    "mobi": ("#fef3c7", "#b45309"),
    "azw3": ("#fef3c7", "#b45309"),
    "fb2": ("#e0f2fe", "#0369a1"),
    "djvu": ("#ede9fe", "#6d28d9"),
    "txt": ("#f1f5f9", "#475569"),
}
_DEFAULT_FORMAT_COLOR = ("#e0e7ff", "#4338ca")

SEARCH_CARD_TMPL = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { background: #f1f5f9; }
  body {
    width: 800px;
    padding: 24px;
    background: linear-gradient(160deg, #eef2ff 0%, #f8fafc 45%, #f1f5f9 100%) no-repeat;
    font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", "Source Han Sans SC", sans-serif;
    color: #0f172a;
  }
  .head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    padding: 0 6px 16px;
  }
  .head .q { font-size: 22px; font-weight: 800; letter-spacing: .5px; }
  .head .q span { color: #4f46e5; }
  .head .meta { font-size: 14px; color: #64748b; white-space: nowrap; margin-left: 16px; }
  .item {
    display: flex;
    align-items: stretch;
    background: #ffffff;
    border-radius: 16px;
    padding: 16px;
    margin-bottom: 14px;
    box-shadow: 0 4px 16px rgba(15, 23, 42, 0.07);
    border: 1px solid rgba(148, 163, 184, 0.25);
  }
  .cover {
    position: relative;
    width: 96px;
    height: 136px;
    flex-shrink: 0;
    border-radius: 8px;
    overflow: hidden;
    background: linear-gradient(135deg, #c7d2fe 0%, #e2e8f0 100%);
    box-shadow: 0 3px 10px rgba(15, 23, 42, 0.18);
  }
  .cover .ph {
    position: absolute; inset: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 40px; font-weight: 800; color: rgba(79, 70, 229, 0.55);
  }
  .cover img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
  .no {
    position: absolute; top: 0; left: 0;
    min-width: 28px; height: 28px; padding: 0 6px;
    display: flex; align-items: center; justify-content: center;
    background: #4f46e5; color: #fff;
    font-size: 15px; font-weight: 800;
    border-bottom-right-radius: 10px;
  }
  .info { flex: 1; min-width: 0; margin-left: 18px; display: flex; flex-direction: column; }
  .title {
    font-size: 18px; font-weight: 700; line-height: 1.4; color: #0f172a;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; word-break: break-all;
  }
  .author {
    margin-top: 6px; font-size: 14px; color: #475569;
    overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
  }
  .pub {
    margin-top: 3px; font-size: 13px; color: #94a3b8;
    overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
  }
  .tags { margin-top: auto; padding-top: 10px; white-space: nowrap; }
  .tag {
    display: inline-block; margin-right: 8px;
    padding: 3px 11px; border-radius: 999px;
    font-size: 13px; font-weight: 600;
    background: #f1f5f9; color: #475569;
  }
  .tag.fmt { letter-spacing: .5px; }
  .foot {
    text-align: center; padding: 6px 0 2px;
    font-size: 14px; color: #64748b;
  }
</style>
</head>
<body>
  <div class="head">
    <div class="q">搜索 <span>{{ query | e }}</span></div>
    <div class="meta">{{ meta | e }}</div>
  </div>
  {% for b in books %}
  <div class="item">
    <div class="cover">
      <div class="ph">书</div>
      {% if b.cover %}<img src="{{ b.cover | e }}" onerror="this.style.display='none'">{% endif %}
      <div class="no">{{ b.no | e }}</div>
    </div>
    <div class="info">
      <div class="title">{{ b.title | e }}</div>
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
  <div class="foot">{{ footer | e }}</div>
</body>
</html>
"""


def build_card_data(result: SearchResult, covers: dict[int, str], prefix: str) -> dict:
    """构造模板数据。

    covers: book id -> data URI（缺失表示用占位）；
    prefix: 指令前缀，用于页脚提示（例如 "/zlib"）。
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
    hints = [f"回复 {prefix} 下载 序号 获取文件"]
    if result.has_next:
        hints.append(f"{prefix} 下一页 翻页")
    return {
        "query": flatten_field(result.query, 60),
        "meta": meta,
        "books": books,
        "footer": "，".join(hints),
    }
