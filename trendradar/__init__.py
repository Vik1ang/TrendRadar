"""
TrendRadar - 热点新闻聚合与分析工具

使用方式:
  python -m trendradar        # 模块执行
  trendradar                  # 安装后执行
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trendradar.context import AppContext


def __getattr__(name: str) -> Any:
    if name == "AppContext":
        from trendradar.context import AppContext

        return AppContext
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*globals(), "AppContext"])

__version__ = "6.11.0"
__all__ = ["AppContext", "__version__"]
