"""无需完整 AstrBot 安装的 Tor 适配冒烟测试。"""

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

# 导入必须在 sys.path 注入之后：本脚本不依赖完整 AstrBot 安装，
# 上面的桩模块与路径是导入这些模块的前提。
from astrbot_plugin_zlibrary_assistant.zlib_client import (  # noqa: E402
    ZlibClient,
    _normalize_base_url,
)
from astrbot_plugin_zlibrary_assistant.zlib_security import (  # noqa: E402
    is_safe_public_url,
)


async def main() -> None:
    host = "a" * 56 + ".onion"
    assert _normalize_base_url(host) == f"http://{host}"
    assert is_safe_public_url(f"http://{host}/x", allow_onion=True)[0]
    assert not is_safe_public_url("http://127.0.0.1/x", allow_onion=True)[0]

    client = ZlibClient(accounts=[], domain=host, proxy="socks5://127.0.0.1:9050")
    session = await client._get_session()
    assert session.connector is not None
    assert client._proxy_kwargs() == {}
    await client.close()
    print("onion adapter smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())
