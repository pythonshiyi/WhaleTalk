# -*- mode: python ; coding: utf-8 -*-
# 鲸语 WhaleTalk Web 打包配置（PyInstaller）
# 用法：pyinstaller WhaleTalk.spec --noconfirm
# 入口为 Web 版（v3.1）：api_server 同源服务 webui/dist，web_app.py 启动本地 API +
# 打开浏览器 + 系统托盘常驻（不再有 pywebview 原生窗口）。
# 大型可选依赖（playwright / faster-whisper / PyMuPDF 等）未安装时自动禁用，
# 此处显式排除，保持体积可控。

import os

_webui_dist = os.path.join(os.path.dirname(SPEC), 'webui', 'dist')
_datas = [
    ('static', 'static'),
    ('templates', 'templates'),
    ('sample_plugins', 'sample_plugins'),
    ('evolutions', 'evolutions'),
    ('app.ico', '.'),
]
if os.path.isdir(_webui_dist):
    _datas.append((_webui_dist, 'webui/dist'))

a = Analysis(
    ['web_app.py'],
    pathex=[],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        'tiktoken_ext.openai_public',
        'pystray',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'playwright',
        'faster_whisper',
        'pyzbar',
        'psycopg2',
        'fitz',
        'reportlab',
        'imageio_ffmpeg',
        'curl_cffi',
        'pptx',
        'PySide6',
        'PyQt5',
        'IPython',
        'pytest',
        'webview',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WhaleTalk',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app.ico'],
)
