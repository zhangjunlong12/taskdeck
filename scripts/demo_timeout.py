"""演示脚本：故意运行很久，用于验证超时后进程会被强制终止。

配合任务超时时间（例如 3 秒）使用，期望运行记录状态为 timeout。
"""
import time

print("start：接下来将睡眠 30 秒，若任务超时为 3 秒，本进程应被强制终止", flush=True)
time.sleep(30)
print("never：这一行不应该被打印出来")
