# -*- coding: utf-8 -*-
"""运行时工具域模块聚合（P0-1 巨石拆分首批）。

deepseek_client.py 在共享基建定义后执行 `from agent_tools import *`：
  - 各域模块顶层运行 @tool() 注册（toolkit 注册表是模块级单例，跨模块共享）；
  - 本包 __all__ 显式 re-export 工具函数名，保证 dc.get_date / dc.read_csv 等
    旧访问路径（外部代码经 deepseek_client 命名空间访问）保持不变。

加载顺序契约：deepseek_client 必须先完成本包 from-import 的符号定义
（如 tool_basic 用到的 WEATHER_TIMEOUT），故导入语句位于主文件共享基建之后、
六层构建之前（见 deepseek_client.py 对应注释）。

新增工具域：新建 tool_*.py 并在下方 import；若含新工具名记得同步 __all__。
"""

from .tool_basic import *  # noqa: F401,F403  # get_date / get_weather
from .tool_data import *   # noqa: F401,F403  # read_csv / write_csv

__all__ = [
    "get_date",
    "get_weather",
    "read_csv",
    "write_csv",
]
