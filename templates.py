"""搜索结果的 HTML/Jinja2 卡片模板。

用于 AstrBot 文转图（html_renderer.render_custom_template）：
把书籍列表渲染成一张竖版卡片图片（封面 + 标题 + 作者 + 元信息）。

注意：
- 字体用 CSS 字体栈，云端 t2i 渲染时自动选择可用中文字体，不依赖本地
- 封面图用 <img src> 引用 Z-Library CDN，加载失败时 onerror 隐藏（不影响文字信息）
"""

SEARCH_CARD_TMPL = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; }
  body {
    font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", "Source Han Sans SC", sans-serif;
    background: #f2f4f8;
    margin: 0;
    padding: 18px;
    width: 720px;
  }
  .header {
    font-size: 16px;
    color: #6b7280;
    margin-bottom: 14px;
    padding-left: 2px;
  }
  .header b { color: #374151; }
  .card {
    display: flex;
    align-items: center;
    background: #ffffff;
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 12px;
    box-shadow: 0 2px 8px rgba(17, 24, 39, 0.06);
  }
  .index {
    font-size: 26px;
    font-weight: 800;
    color: #4f46e5;
    width: 44px;
    text-align: center;
    flex-shrink: 0;
  }
  .cover {
    width: 84px;
    height: 120px;
    object-fit: cover;
    border-radius: 6px;
    background: #e5e7eb;
    flex-shrink: 0;
  }
  .info { margin-left: 14px; flex: 1; min-width: 0; }
  .title {
    font-size: 17px;
    font-weight: 700;
    color: #111827;
    margin: 0 0 6px;
    line-height: 1.35;
    word-break: break-all;
  }
  .author {
    font-size: 13px;
    color: #6b7280;
    margin: 0 0 8px;
  }
  .badges { white-space: nowrap; }
  .badge {
    display: inline-block;
    background: #eef2ff;
    color: #4f46e5;
    border-radius: 5px;
    padding: 2px 9px;
    font-size: 12px;
    margin-right: 6px;
  }
  .badge.gray { background: #f3f4f6; color: #4b5563; }
</style>
</head>
<body>
<div class="header">📚 <b>{{ query }}</b> 的搜索结果（{{ books|length }} 本，下载请回复书籍编号）</div>
{% for book in books %}
<div class="card">
  <div class="index">{{ loop.index }}</div>
  {% if book.cover %}
  <img class="cover" src="{{ book.cover }}" onerror="this.style.display='none'">
  {% else %}
  <div class="cover"></div>
  {% endif %}
  <div class="info">
    <div class="title">{{ book.title }}</div>
    <div class="author">{{ book.author }}</div>
    <div class="badges">
      <span class="badge">{{ book.extension|upper }}</span>
      <span class="badge gray">{{ book.language }}</span>
      <span class="badge gray">{{ book.year }}</span>
      <span class="badge gray">{{ book.filesizeString }}</span>
    </div>
  </div>
</div>
{% endfor %}
</body>
</html>
"""
