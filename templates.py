"""搜索结果的 HTML/Jinja2 卡片模板。

用于 AstrBot 文转图（html_renderer.render_custom_template）：
把书籍列表渲染成一张竖版卡片图片（封面 + 标题 + 作者 + 元信息）。

注意：
- 字体用 CSS 字体栈，云端 t2i 渲染时自动选择可用中文字体，不依赖本地
- 封面图用 <img src> 引用 Z-Library CDN；云端渲染器可能无法访问该域名，
  因此封面下方内置渐变占位（📕），封面加载失败时自动露出，卡片不空白
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
  /* 封面容器：内嵌渐变占位，封面加载失败时露出 */
  .cover-wrap {
    position: relative;
    width: 84px;
    height: 120px;
    border-radius: 6px;
    overflow: hidden;
    flex-shrink: 0;
    background: linear-gradient(135deg, #eef2ff 0%, #e5e7eb 100%);
  }
  .cover-fallback {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 34px;
    color: #a5b4fc;
  }
  .cover {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .info { margin-left: 14px; flex: 1; min-width: 0; }
  .title {
    font-size: 16px;
    font-weight: 700;
    color: #111827;
    margin: 0 0 6px;
    line-height: 1.35;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-all;
  }
  .author {
    font-size: 13px;
    color: #6b7280;
    margin: 0 0 8px;
    display: -webkit-box;
    -webkit-line-clamp: 1;
    -webkit-box-orient: vertical;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
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
<div class="header">📚 <b>{{ query }}</b> 的搜索结果（{{ books|length }} 本，需要下载请回复编号）</div>
{% for book in books %}
<div class="card">
  <div class="index">{{ loop.index }}</div>
  <div class="cover-wrap">
    <div class="cover-fallback">📕</div>
    {% if book.cover %}
    <img class="cover" src="{{ book.cover }}" onerror="this.style.display='none'">
    {% endif %}
  </div>
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
