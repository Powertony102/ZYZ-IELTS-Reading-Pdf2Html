#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# IELTS Reading 练习服务一键启动脚本
# 用法: bash start_at_server.sh [PORT]
# ============================================================

# ----- 配置区（按需修改）-----
PORT="7777"                                       # 服务端口，默认 7777
DAEMON=false                                      # 是否后台运行
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"  # 项目根目录

# ----- 参数解析 -----
for arg in "$@"; do
    case "${arg}" in
        -d|--daemon)
            DAEMON=true
            ;;
        -h|--help)
            echo "用法: bash start_at_server.sh [PORT] [-d|--daemon]"
            echo "  PORT          服务端口，默认 7777"
            echo "  -d, --daemon  后台运行（nohup）"
            exit 0
            ;;
        [0-9]*)
            PORT="${arg}"
            ;;
    esac
done
VENV_DIR="${PROJECT_DIR}/.venv"                   # 虚拟环境路径
REQUIREMENTS="${PROJECT_DIR}/requirements.txt"    # 依赖文件
SERVER_SCRIPT="${PROJECT_DIR}/dashboard/server.py" # 服务入口
DB_DIR="${PROJECT_DIR}/dashboard"                 # SQLite 数据库目录

# 切换至项目根目录
cd "${PROJECT_DIR}"

echo "=========================================="
echo "  IELTS Reading 练习服务"
echo "=========================================="
echo "项目目录:   ${PROJECT_DIR}"
echo "服务端口:   ${PORT}"
echo "=========================================="

# ----- 1. 检查 Python -----
echo ""
echo "[1/3] 检查 Python 环境..."
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3，请先安装 Python 3.9+"
    exit 1
fi

PY_VERSION="$(python3 --version 2>&1)"
echo "Python: ${PY_VERSION}"

# ----- 2. 创建虚拟环境并安装依赖 -----
echo ""
echo "[2/3] 创建虚拟环境并安装依赖..."
if [[ ! -d "${VENV_DIR}" ]]; then
    echo "创建虚拟环境 ${VENV_DIR} ..."
    python3 -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"

pip install --upgrade pip -q
if [[ -f "${REQUIREMENTS}" ]]; then
    pip install -r "${REQUIREMENTS}" -q
else
    echo "未找到 ${REQUIREMENTS}，跳过依赖安装"
fi

# 确保数据库目录存在
mkdir -p "${DB_DIR}"

# ----- 3. 启动服务 -----
echo ""
echo "[3/3] 启动 IELTS Reading 服务..."
echo ""
echo "  导航页面: http://127.0.0.1:${PORT}/dashboard/index.html"
echo "  练习页面: 从导航页点击进入，答题状态自动保存到 *.state.json"
echo ""
echo "  按 Ctrl+C 停止服务"
echo "=========================================="
echo ""

if [[ "${DAEMON}" == true ]]; then
    LOG_FILE="${PROJECT_DIR}/server.log"
    nohup "${VENV_DIR}/bin/python" "${SERVER_SCRIPT}" --root "${PROJECT_DIR}" --port "${PORT}" > "${LOG_FILE}" 2>&1 &
    echo ""
    echo "[+] 服务已在后台启动"
    echo "    PID: $!"
    echo "    日志: ${LOG_FILE}"
    echo "    访问: http://127.0.0.1:${PORT}/dashboard/index.html"
else
    python "${SERVER_SCRIPT}" --root "${PROJECT_DIR}" --port "${PORT}"
fi
