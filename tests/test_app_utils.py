"""app_utils 回归：空壳判定按路径隔离 + 隐私日志切换不因缺少 logging.handlers
预导入而抛 AttributeError。"""
import logging
import logging.handlers
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import app_utils  # noqa: E402


def test_is_empty_shell_path_isolated(tmp_path):
    """缓存必须按路径隔离：旧实现以「首次结果」全局缓存，第二次换路径会串用。"""
    empty = tmp_path / "empty"
    (empty / "sub").mkdir(parents=True)  # 仅含空子目录 = 空壳
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "file.txt").write_text("x", encoding="utf-8")

    # 先判非空，再判空壳——旧实现会把非空的 False 串给空壳目录
    assert app_utils.is_empty_shell(str(nonempty)) is False
    assert app_utils.is_empty_shell(str(empty)) is True
    # 反向顺序亦不受缓存污染
    assert app_utils.is_empty_shell(str(empty)) is True
    assert app_utils.is_empty_shell(str(nonempty)) is False


def _file_handlers():
    return [
        h for h in logging.getLogger().handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]


def test_apply_privacy_logging_roundtrip(tmp_path):
    root = logging.getLogger()
    saved = _file_handlers()
    for h in saved:
        root.removeHandler(h)
    try:
        app_utils.apply_privacy_logging(False, str(tmp_path))
        assert len(_file_handlers()) == 1, "关闭隐私时应补回一个文件 handler"
        app_utils.apply_privacy_logging(True, str(tmp_path))
        assert not _file_handlers(), "开启隐私时应移除全部文件 handler"
    finally:
        for h in _file_handlers():
            try:
                h.close()
            except Exception:
                pass
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)
