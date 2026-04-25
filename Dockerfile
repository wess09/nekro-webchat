# 使用 uv 官方提供的轻量级 Python 3.11 镜像作为基础镜像
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

# 设置容器内的运行工作目录
WORKDIR /app

# 复制依赖定义文件
COPY pyproject.toml uv.lock ./

# 使用 uv 安装依赖（包括生成的虚拟环境）
RUN uv sync --frozen --no-dev

# 复制后端应用核心代码以及依赖的 SDK
COPY app/ ./app/
COPY nekro_agent_sse_sdk/ ./nekro_agent_sse_sdk/
COPY static/ ./static/

# 暴露 FastAPI 运行端口
EXPOSE 8765

# 设置环境变量，确保服务可以被外部访问
ENV HOST=0.0.0.0
ENV PORT=8765

# 启动命令：通过 uv 启动 uvicorn 服务
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
