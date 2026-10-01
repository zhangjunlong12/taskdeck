"""演示脚本：逐行输出，用于验证实时输出流式回传与中文编码。

故意在第 2 行后停顿 1 秒，方便观察界面上的输出是否逐行出现而不是一次性刷出。
"""
import sys
import time

print("第 1 行 · 中文输出测试 · 开始")
sys.stdout.flush()

time.sleep(1)
print("第 2 行 · 如果你能逐行看到，说明实时输出正常")
sys.stdout.flush()

time.sleep(1)
print("第 3 行 · 含特殊字符：✅ 完成 100% · 温度 25℃")
sys.stdout.flush()

time.sleep(1)
print("done")
