"""python -m wechat_writer 入口（--dry-run / --run / --topic）。"""
import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
