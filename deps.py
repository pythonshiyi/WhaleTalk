# -*- coding: utf-8 -*-
"""可选依赖清单（依赖状态对话框）。

从 main.py 中拆出，供依赖检测/UI 展示使用。
格式：(导入名, 显示名, 影响功能, 安装命令)
"""

OPTIONAL_DEPS = [
    ("PIL", "Pillow", "图片处理/应用内图片预览/OCR/图表/图标", "pip install pillow"),
    ("pyautogui", "pyautogui", "桌面 RPA（鼠标/键盘/屏幕坐标）", "pip install pyautogui"),
    ("pystray", "pystray", "系统托盘常驻", "pip install pystray"),
    ("playwright", "playwright", "浏览器操作/网页截图", "pip install playwright && playwright install chromium"),
    ("faster_whisper", "faster-whisper", "语音转文字", "pip install faster-whisper"),
    ("sounddevice", "sounddevice", "实时语音对话录音（voice_chat_loop）", "pip install sounddevice numpy"),
    ("edge_tts", "edge-tts", "在线神经网络音色/更自然朗读（缺失自动回退 SAPI）", "pip install edge-tts"),
    ("fitz", "PyMuPDF", "PDF 提取", "pip install PyMuPDF"),
    ("reportlab", "reportlab", "PDF 生成", "pip install reportlab"),
    ("docx", "python-docx", "Word 读写", "pip install python-docx"),
    ("pptx", "python-pptx", "PPT 读取", "pip install python-pptx"),
    ("feedparser", "feedparser", "RSS 聚合/每日简报/公众号写作", "pip install feedparser"),
    ("qrcode", "qrcode", "二维码生成", "pip install qrcode"),
    ("pyzbar", "pyzbar", "二维码识别", "pip install pyzbar（另需系统 zbar）"),
    ("diskcache", "diskcache", "KV 存储", "pip install diskcache"),
    ("imageio_ffmpeg", "imageio-ffmpeg", "音视频处理（内置 ffmpeg）", "pip install imageio-ffmpeg"),
    ("markdown", "markdown", "公众号写作 HTML 输出", "pip install markdown"),
    ("win32com", "pywin32", "语音朗读/语音合成", "pip install pywin32"),
    ("tiktoken", "tiktoken", "精确 token 估算（缺省回退字符估算）", "pip install tiktoken"),
    ("pygments", "Pygments", "代码块语法高亮", "pip install pygments"),
    ("ebooklib", "ebooklib", "EPUB 电子书阅读", "pip install ebooklib"),
    ("mobi", "mobi", "MOBI 电子书阅读", "pip install mobi"),
    ("extract_msg", "extract-msg", "Outlook .msg 邮件阅读", "pip install extract-msg"),
    ("py7zr", "py7zr", "7z 压缩包", "pip install py7zr"),
    ("rarfile", "rarfile", "RAR 压缩包（另需 unrar/unar）", "pip install rarfile"),
]

# ── 启动自检：自动安装清单（缺了影响基础体验，首启用清华源自动装）────
# 格式：(导入名, pip 包名, 显示名)
AUTO_INSTALL_DEPS = [
    ("openai", "openai", "核心 API 网关"),
    ("httpx", "httpx", "网络请求"),
    ("pystray", "pystray", "系统托盘图标"),
    ("win32com", "pywin32", "托盘 / 语音"),
    ("PIL", "Pillow", "图片处理 / 图标"),
    ("cryptography", "cryptography", "大脑免密快照"),
    ("tiktoken", "tiktoken", "token 精确估算"),
    ("pygments", "pygments", "代码高亮"),
    ("markdown", "markdown", "公众号 HTML 输出"),
    ("diskcache", "diskcache", "KV 存储"),
    ("psutil", "psutil", "系统自检"),
    ("pyautogui", "pyautogui", "桌面 RPA"),
    ("fitz", "PyMuPDF", "PDF 提取"),
    ("reportlab", "reportlab", "PDF 生成"),
    ("docx", "python-docx", "Word 读写"),
    ("pptx", "python-pptx", "PPT 读取"),
    ("feedparser", "feedparser", "RSS 聚合"),
    ("qrcode", "qrcode", "二维码生成"),
    ("imageio_ffmpeg", "imageio-ffmpeg", "音视频处理"),
    ("openpyxl", "openpyxl", "Excel 读写"),
    ("matplotlib", "matplotlib", "数据图表"),
    ("curl_cffi", "curl_cffi", "被墙站点抓取"),
    ("sounddevice", "sounddevice", "实时语音录音"),
    ("edge_tts", "edge-tts", "在线神经音色"),
]

# ── 重型 / 需系统组件：可选安装（首启弹窗勾选，用户取舍）──────────────────
# 字段：import(导入名) label(能力名) desc(能力说明) pip(pip包名, 可空)
#       post_cmd(装后额外命令, 可空) note(补充提示)
HEAVY_DEPS = [
    {
        "import": "playwright",
        "label": "浏览器自动化 / 网页截图",
        "desc": "控制浏览器、抓取网页、页面截图",
        "pip": "playwright",
        "post_cmd": ["playwright", "install", "chromium"],
        "note": "会额外下载 Chromium（约 150 MB）",
    },
    {
        "import": "faster_whisper",
        "label": "本地语音转写",
        "desc": "离线把语音转成文字（不联网）",
        "pip": "faster-whisper",
        "post_cmd": None,
        "note": "首次转写时下载模型（约 300 MB）",
    },
    {
        "import": "pyzbar",
        "label": "二维码识别",
        "desc": "识别图片中的二维码 / 条码",
        "pip": "pyzbar",
        "post_cmd": None,
        "note": "另需系统 zbar 库（pip 不含 DLL，可能仍需手动装）",
    },
    {
        "import": "rarfile",
        "label": "RAR 解压",
        "desc": "解压 RAR 压缩包",
        "pip": "rarfile",
        "post_cmd": None,
        "note": "另需 unrar 命令（可下载 UnRAR.exe 放 PATH）",
    },
]

