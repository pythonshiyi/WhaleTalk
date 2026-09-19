"""mvrender.gpu —— OpenCL 后端（AMD RX 9060 XT / gfx1200 优先）。"""
from .canvas_gpu import GPUCanvas
from .runtime import GPUUnavailable, Runtime

__all__ = ["Runtime", "GPUCanvas", "GPUUnavailable"]
