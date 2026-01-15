#!/bin/bash
# 快速启动脚本

echo "================================"
echo "LLM 记忆系统启动脚本"
echo "================================"

# 检查 Python 环境
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 Python 3"
    exit 1
fi

# 检查是否在虚拟环境中
if [ -z "$VIRTUAL_ENV" ]; then
    echo "警告: 未在虚拟环境中运行"
    echo "建议先激活虚拟环境: source venv/bin/activate"
    read -p "是否继续? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 检查依赖
echo "检查依赖..."
pip list | grep -q fastapi
if [ $? -ne 0 ]; then
    echo "正在安装依赖..."
    pip install -r requirements.txt
fi

# 检查环境变量文件
if [ ! -f .env ]; then
    echo "警告: 未找到 .env 文件"
    echo "请创建 .env 文件并配置必要的环境变量"
    echo "参考 .env.example 文件"
    exit 1
fi

# 检查 Firebase 凭证
CRED_FILE=$(grep GOOGLE_APPLICATION_CREDENTIALS .env | cut -d '=' -f2)
if [ ! -f "$CRED_FILE" ]; then
    echo "警告: 未找到 Firebase 凭证文件: $CRED_FILE"
    exit 1
fi

echo "✓ 环境检查通过"
echo ""
echo "启动服务器..."
echo "API 文档: http://localhost:8000/docs"
echo "================================"
echo ""

# 启动服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
