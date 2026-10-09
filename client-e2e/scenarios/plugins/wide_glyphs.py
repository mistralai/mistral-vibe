"""Double-width text wraps and folds by terminal columns, so no part of a CJK description is lost."""

from __future__ import annotations

from e2e.app_server.plugins import METHODS, catalog, entry
from e2e.app_server.scenario import Timeline, resize

_CJK = entry(
    "cjk",
    "0123456789abcdef0123456789abcdef",
    description=(
        "插件描述用于测试双宽字符在窄终端中的折行显示效果并确保末尾文字可见"
        " 第二段文字 第三段文字 第四段文字 第五段文字"
    ),
)

handshake = {"plugin_catalog/read": catalog([_CJK])}
request_methods = METHODS

timeline: Timeline = [resize(30, 40), "/plugins\r", "\r"]

screen_contains = {"rust": ("末尾文字可",)}
