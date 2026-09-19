"""mvrender —— FastMV Engine：GPU 为主的程序化 MV 渲染引擎。

定位
----
一次建立、后续所有 MV 复用。分层：

    mvrender.core    平台无关：Renderer / camera / transition / lyrics / project / cache
    mvrender.gpu     OpenCL 后端：runtime / kernels / canvas_gpu / ops

与 WhaleTalk 现有 `mv_engine.py`（音频分析 + 对轴 + 分镜 + PIL 帧）互补：
`mv_engine` 负责「听与卡点」，`mvrender` 负责「画与合成」。两者通过 project
契约（timeline/shots）衔接。
"""
from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
