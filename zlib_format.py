"""文本输出：帮助、状态、诊断，以及指令参数解析。纯函数，不依赖 AstrBot。"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from urllib.parse import urlparse

from .zlib_client import Diagnosis
from .zlib_domains import ProbeReport

CATEGORY_LABELS = {
    "network_error": "网络不通",
    "tls_error": "证书/TLS 失败",
    "diamwall_blocked": "DiamWall 验证拦截",
    "cloudflare_blocked": "Cloudflare 验证拦截",
    "rate_limited": "IP 被限流",
    "domain_invalid": "域名失效",
    "auth_failed": "需要登录",
}

HELP_TEXT = (
    "Z-Library 助手指令：\n"
    "{p} 搜索 关键词 [-l 语言] [-e 格式] [-n 条数]\n"
    "    例：{p} 搜索 资本论 -l zh -e pdf\n"
    "{p} 详情 序号   看某本书的完整信息与简介\n"
    "{p} 下载 序号   按最近一次搜索结果的序号下载（也可填书的 id）\n"
    "{p} 下一页 / {p} 上一页   翻页\n"
    "{p} 状态   账号与额度（管理员）\n"
    "{p} 诊断   探测镜像域名与账号（管理员）\n"
    "搜索与查看详情不消耗下载额度；语言可写 zh/en/ja 等或 chinese/english 等。"
)


@dataclass
class SearchArgs:
    query: str
    language: str = ""
    extension: str = ""
    limit: int | None = None


class ArgError(ValueError):
    """指令参数不合法；消息可直接回显给用户。"""


def strip_command(text: str, sub_names: set[str]) -> str:
    """去掉消息开头的“指令组 + 子指令”，返回其后的参数部分。

    `text` 形如 "zlib 搜索 资本论 -l zh"（唤醒前缀可能带或不带 "/"）；
    组名与别名不固定，因此取第二个词起第一个命中子指令名的位置。
    """
    tokens = text.strip().lstrip("/").split(maxsplit=2)
    if len(tokens) >= 2 and tokens[1] in sub_names:
        return tokens[2] if len(tokens) == 3 else ""
    return ""


def parse_search_args(arg_text: str) -> SearchArgs:
    """解析 `关键词 [-l 语言] [-e 格式] [-n 条数]`；未带选项的词都并入关键词。"""
    try:
        tokens = shlex.split(arg_text, posix=False)
    except ValueError as e:
        raise ArgError(f"参数无法解析：{e}") from e
    words: list[str] = []
    args = SearchArgs(query="")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-l", "-e", "-n"):
            if i + 1 >= len(tokens):
                raise ArgError(f"选项 {tok} 后需要一个值")
            value = tokens[i + 1].strip("\"'")
            if tok == "-l":
                args.language = value
            elif tok == "-e":
                args.extension = value
            else:
                if not value.isdigit() or int(value) < 1:
                    raise ArgError("-n 后需要正整数")
                args.limit = int(value)
            i += 2
            continue
        words.append(tok.strip("\"'") if len(tok) > 1 and tok[0] in "\"'" else tok)
        i += 1
    args.query = " ".join(words).strip()
    if not args.query:
        raise ArgError("请提供搜索关键词，例如：搜索 资本论")
    return args


def _host(base_url: str) -> str:
    return urlparse(base_url).hostname or base_url


def format_status(rows: list[dict]) -> str:
    if not rows:
        return "未配置任何账号：搜索可匿名使用，下载需要在插件配置中添加账号。"
    lines = ["Z-Library 账号池："]
    for r in rows:
        state = "正常" if r["logged_in"] else "未登录"
        vip = "，Premium" if r.get("premium") else ""
        lines.append(
            f"- {r['name']}（{r['login_mode']}）{state}{vip}，"
            f"今日 {r['downloads_today']}/{r['downloads_limit']}，剩余 {r['downloads_left']}"
        )
        if r.get("last_error"):
            lines.append(f"  最近错误：{r['last_error']}")
    return "\n".join(lines)


def _format_probe(report: ProbeReport) -> str:
    host = _host(report.base_url)
    if report.ok:
        return f"- {host}  可用  {report.latency_ms} ms"
    label = CATEGORY_LABELS.get(report.category, report.category)
    return f"- {host}  不可用（{label}）"


def format_diagnosis(diag: Diagnosis) -> str:
    ok = [d for d in diag.domains if d.ok]
    lines = [f"镜像域名：{len(ok)}/{len(diag.domains)} 可用"]
    lines += [_format_probe(d) for d in diag.domains]
    lines.append(f"代理：{diag.proxy or '未配置'}")
    lines.append(format_status(diag.accounts))
    if not ok:
        lines.append(
            "所有镜像都不可用：检查服务器能否出网，或在配置的 domains 中补充可用镜像；"
            "官方主域启用了 DiamWall 浏览器验证，无法直连。"
        )
    return "\n".join(lines)
