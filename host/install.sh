#!/usr/bin/env bash
# 热币交易主机一键安装（Ubuntu 22.04 / 24.04，用 root 运行）。可以重复运行，重复运行 = 更新。
# 用法：curl -fsSL https://raw.githubusercontent.com/ivonasmith/hotcoin-trade-automation/main/host/install.sh | sudo bash
set -euo pipefail
REPO_URL="https://github.com/ivonasmith/hotcoin-trade-automation.git"
APP=/opt/hotcoin
ENV_FILE=/etc/hotcoin/host.env

if [ "$(id -u)" -ne 0 ]; then echo "请用 root 运行（前面加 sudo）"; exit 1; fi
say() { printf '\n== %s\n' "$*"; }

say "0/6 交换空间（内存只有 2GB 时出图要用）"
MEM_MB="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)"
if [ "$MEM_MB" -lt 3500 ] && ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "已加 2GB 交换空间"
fi

say "1/6 系统组件"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl ca-certificates fonts-noto-cjk fonts-liberation
NODE_MAJOR=0
if command -v node >/dev/null 2>&1; then NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"; fi
if [ "$NODE_MAJOR" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

say "2/6 程序用户和目录"
id hotcoin >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin hotcoin
mkdir -p "$APP" /var/lib/hotcoin /etc/hotcoin
chown -R hotcoin:hotcoin /var/lib/hotcoin "$APP"

say "3/6 下载代码"
if [ -d "$APP/repo/.git" ]; then
  sudo -u hotcoin git -C "$APP/repo" pull --ff-only
else
  sudo -u hotcoin git clone "$REPO_URL" "$APP/repo"
fi

say "4/6 Python 依赖和出图浏览器（这一步要几分钟）"
[ -d "$APP/venv" ] || sudo -u hotcoin python3 -m venv "$APP/venv"
sudo -u hotcoin "$APP/venv/bin/pip" install -q --upgrade pip
sudo -u hotcoin "$APP/venv/bin/pip" install -q -r "$APP/repo/pipeline/requirements.txt" -r "$APP/repo/poster/requirements.txt"
"$APP/venv/bin/python" -m playwright install-deps chromium
sudo -u hotcoin env PLAYWRIGHT_BROWSERS_PATH=/home/hotcoin/.cache/ms-playwright "$APP/venv/bin/python" -m playwright install chromium

say "5/6 密钥文件 $ENV_FILE"
if [ ! -s "$ENV_FILE" ]; then
  if [ -r /dev/tty ]; then
    echo "接下来粘贴三个值（输入时屏幕不显示，粘贴后回车；Notion 没有就直接回车）。"
    read -r -p "WORKER_INGEST_URL（后台网址，https:// 开头）: " V_URL < /dev/tty
    read -r -s -p "PIPELINE_SECRET: " V_SECRET < /dev/tty; echo
    read -r -s -p "NOTION_TOKEN: " V_NOTION < /dev/tty; echo
    umask 077
    {
      echo "WORKER_INGEST_URL=$V_URL"
      echo "PIPELINE_SECRET=$V_SECRET"
      [ -n "$V_NOTION" ] && echo "NOTION_TOKEN=$V_NOTION"
    } > "$ENV_FILE"
  else
    echo "没有终端可输入，创建了空模板，请自己编辑 $ENV_FILE 后重新运行本脚本。"
    umask 077
    printf 'WORKER_INGEST_URL=\nPIPELINE_SECRET=\nNOTION_TOKEN=\n' > "$ENV_FILE"
  fi
fi
chown root:hotcoin "$ENV_FILE"; chmod 640 "$ENV_FILE"

say "6/6 开机自启"
install -m 644 "$APP/repo/host/systemd/hotcoin-host.service" /etc/systemd/system/hotcoin-host.service
systemctl daemon-reload
systemctl enable hotcoin-host >/dev/null
echo
echo "---- 网络自检 ----"
sudo -u hotcoin env HOTCOIN_ENV_FILE="$ENV_FILE" "$APP/venv/bin/python" "$APP/repo/host/check_network.py" || NET_FAIL=1
if [ "${NET_FAIL:-0}" = 1 ]; then
  echo
  echo "有必需项不通，先不启动服务。把上面的结果发给 Claude。（修好后重新运行本脚本）"
  exit 1
fi
systemctl restart hotcoin-host
sleep 3
systemctl --no-pager --lines=8 status hotcoin-host || true
echo
echo "安装完成。在 Telegram 里给机器人发 /host，能看到主机心跳就对了。"
