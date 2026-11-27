"""文本与图片的纯函数工具。"""

from __future__ import annotations

import io
import re
import warnings

from PIL import Image

# 封面解码像素预算：PIL 解码不可信字节时按声明的宽高分配内存，一张几十 KB 的
# PNG 可以把 header 声明成几万 × 几万，解码即 OOM。同时开启 warnings->error，
# 让 PIL 自身的 DecompressionBomb 阈值成为第二道闸
MAX_COVER_PIXELS = 40_000_000

# 数据围栏标记：书目字段进入 LLM 上下文时包在这对标记里
FENCE_OPEN = "<<<ZLIB_BOOK_DATA"
FENCE_CLOSE = ">>>"


def fix_mojibake(text: str) -> str:
    """修复双重编码文本。

    现象：含重音字符的字段（如作者名 Frédéric）会变成 FrÃ©dÃ©ric，
    原因是服务器端把 UTF-8 字节按 Latin-1 解码后又按 UTF-8 编码发送。

    修复：尝试按 latin-1 编码回原始字节，再按 utf-8 解码。
    仅当文本全部落在 Latin-1 范围内时才转换，中文等不受影响（会原样返回）。
    """
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def fix_mojibake_recursive(obj):
    """递归修复 JSON 结构中所有字符串的 mojibake（在 json.loads 之后调用）。"""
    if isinstance(obj, str):
        return fix_mojibake(obj)
    if isinstance(obj, list):
        return [fix_mojibake_recursive(item) for item in obj]
    if isinstance(obj, dict):
        return {key: fix_mojibake_recursive(value) for key, value in obj.items()}
    return obj


def sanitize_filename(filename: str) -> str:
    """清洗文件名为跨平台安全格式。

    书名可能含 Windows 非法字符（\\ / : * ? " < > |）或控制字符，
    直接保存会触发 OSError；统一替换为下划线，并去掉首尾点/空格。
    """
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", filename).strip(" .")
    return cleaned or "book"


def flatten_field(value, limit: int = 120) -> str:
    """把第三方字段压成单行并限长，同时抹掉数据围栏标记。

    字段来自 Z-Library API：换行能伪造出新的列表行或工具指令形态，超长会挤占
    上下文，字段里自带的围栏标记则能让后续文本跑到围栏之外。
    """
    flat = re.sub(r"[\r\n]+", " ", str(value if value is not None else ""))
    # 嵌套输入删掉内层标记后会重新拼出完整标记，需反复清理到不再变化
    while True:
        cleaned = flat.replace(FENCE_OPEN, "").replace(FENCE_CLOSE, "")
        if cleaned == flat:
            break
        flat = cleaned
    return flat[:limit]


def guess_image_mime(content: bytes) -> str:
    """按文件头魔数判断图片真实类型（比响应头 Content-Type 可靠）。"""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def resize_cover(content: bytes, max_width: int = 240) -> bytes:
    """缩放封面并转 JPEG，压缩 base64 体积（卡片封面显示宽 96px，2.5x 清晰度 240px）。

    content 是封面 CDN 返回的不可信字节（体积上限由调用方限定）。解码前先查像素
    预算，并让 PIL 的 DecompressionBombWarning 升级为异常：按 header 声明的宽高
    分配内存的解码方式是内存放大器，超预算即放弃。

    无法解码或超预算时返回 b""（调用方按占位降级）。绝不回传原始字节：
    原图可能是被中间人替换的任意内容，回传等于把它 base64 内嵌进发给用户的模板。
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(content))
            if img.width * img.height > MAX_COVER_PIXELS:
                return b""
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize(
                    (max_width, max(1, int(img.height * ratio))), Image.LANCZOS
                )
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=86)
            return buf.getvalue()
    except Exception:  # noqa: BLE001 - 解码/超预算失败一律降级为占位
        return b""
