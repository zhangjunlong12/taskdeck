"""演示脚本：故意以非零退出码结束，用于验证失败自动重试。

配合重试次数（例如 2 次）使用，期望产生 attempt 1/2/3 共 3 条运行记录。
"""
import sys

print("尝试执行，模拟失败", flush=True)
sys.exit(7)
