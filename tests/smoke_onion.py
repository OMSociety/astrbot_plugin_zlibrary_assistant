"""Tor onion 适配冒烟脚本（手动运行，不属于 pytest 收集范围）。

    python tests/smoke_onion.py

不需要完整 AstrBot 安装，也不联网：只核对 onion 域名规范化、SSRF 校验的 onion 开关，
以及配置 SOCKS 代理后 ZlibClient 的传输层装配（连接器由代理接管，请求不带 proxy 参数）。
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import types
from pathlib import Path


class _Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


astrbot = types.ModuleType("astrbot")
api = types.ModuleType("astrbot.api")
api.logger = _Logger()
core = types.ModuleType("astrbot.core")
utils = types.ModuleType("astrbot.core.utils")
astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")
astrbot_path.get_astrbot_data_path = tempfile.gettempdir
sys.modules.update(
    {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.core": core,
        "astrbot.core.utils": utils,
        "astrbot.core.utils.astrbot_path": astrbot_path,
    }
)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 导入必须在 sys.path 与桩模块注入之后。
from astrbot_plugin_zlibrary_assistant.zlib_client import ZlibClient  # noqa: E402
from astrbot_plugin_zlibrary_assistant.zlib_domains import (  # noqa: E402
    DomainPool,
    normalize_base_url,
)
from astrbot_plugin_zlibrary_assistant.zlib_security import (  # noqa: E402
    is_safe_public_url,
)

SOCKS_PROXY = "socks5://127.0.0.1:9050"


async def main() -> None:
    host = "a" * 56 + ".onion"
    assert normalize_base_url(host) == f"http://{host}"
    assert is_safe_public_url(f"http://{host}/x", allow_onion=True)[0]
    assert not is_safe_public_url(f"http://{host}/x")[0]
    assert not is_safe_public_url("http://127.0.0.1/x", allow_onion=True)[0]

    try:
        DomainPool([host])
    except ValueError:
        pass
    else:
        raise AssertionError("未配置 SOCKS 代理时应拒绝 onion 域名")

    client = ZlibClient([], domains=[host], proxy=SOCKS_PROXY)
    try:
        assert client.transport.socks
        assert client.domains.primary == f"http://{host}"
        session = await client.transport.get_session()
        assert session.connector is not None
        assert client.transport.proxy_kwargs() == {}
    finally:
        await client.close()
    print("onion adapter smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())
