"""执行上下文组装（§4-§6）：从 workspace 只读构建 ExecutorInput。"""

from .builder import build_executor_input

__all__ = ["build_executor_input"]
