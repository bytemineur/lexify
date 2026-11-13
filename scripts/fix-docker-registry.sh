#!/usr/bin/env bash
# 排查并修复「拉不到 Docker Hub 镜像」：逐个探测加速器，把能用的写进 daemon.json。
#
#   sudo bash scripts/fix-docker-registry.sh          # 探测 + 写入 + 重启 docker
#   sudo bash scripts/fix-docker-registry.sh --dry    # 只探测，不改配置
#
# 背景：registry-1.docker.io 在国内常被 DNS 污染（解析到 157.240.x / 69.171.x
# 等 Facebook 网段），表现为 connection refused 或 i/o timeout，放行 443 无效。
set -uo pipefail

DRY=0
[ "${1:-}" = "--dry" ] && DRY=1

# 顺序即优先级：云厂商内网源在前，社区源兜底。停服的（中科大、网易）不要加。
MIRRORS=(
  "https://mirror.ccs.tencentyun.com"
  "https://docker.m.daocloud.io"
  "https://docker.1ms.run"
  "https://docker.1panel.live"
  "https://dockerproxy.net"
  "https://docker.nju.edu.cn"
  "https://mirror.baidubce.com"
)

echo "== 探测加速器（/v2/ 返回 200 或 401 即为可用）=="
OK=()
for m in "${MIRRORS[@]}"; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$m/v2/" 2>/dev/null || echo 000)
  case "$code" in
    200|401) printf '  \033[32m可用\033[0m  %-40s HTTP %s\n' "$m" "$code"; OK+=("$m") ;;
    *)       printf '  ---     %-40s HTTP %s\n' "$m" "$code" ;;
  esac
done

if [ ${#OK[@]} -eq 0 ]; then
  echo
  echo "没有可用的加速器。改用离线镜像："
  echo "  本机  docker save lexify:latest | gzip -1 > lexify-image.tgz"
  echo "        scp lexify-image.tgz ubuntu@服务器:/opt/"
  echo "  服务器 gunzip -c /opt/lexify-image.tgz | sudo docker load"
  echo "        cd /opt/lexify && sudo docker compose up -d --no-build"
  exit 1
fi

echo
echo "== 可用加速器：${#OK[@]} 个 =="
if [ "$DRY" = "1" ]; then
  echo "（--dry，不修改 /etc/docker/daemon.json）"
  exit 0
fi

command -v python3 >/dev/null || { echo "需要 python3 来合并 JSON"; exit 1; }

# 保留 daemon.json 里已有的键（runtimes、insecure-registries 之类），只覆盖 registry-mirrors
python3 - "${OK[@]}" <<'PY'
import json, os, sys

path = "/etc/docker/daemon.json"
cfg = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        print("现有 daemon.json 不是合法 JSON：%s" % exc)
        print("已备份到 daemon.json.bak，请手工检查后重跑")
        os.replace(path, path + ".bak")
        cfg = {}
    else:
        if cfg.get("registry-mirrors"):
            print("原有 registry-mirrors：%s" % cfg["registry-mirrors"])

cfg["registry-mirrors"] = sys.argv[1:]
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
    f.write("\n")
print("已写入 %s：" % path)
print(json.dumps(cfg, indent=2, ensure_ascii=False))
PY

echo
echo "== 重启 docker =="
systemctl daemon-reload && systemctl restart docker || { echo "重启失败，看 journalctl -u docker"; exit 1; }
sleep 2
docker info 2>/dev/null | grep -A $(( ${#OK[@]} + 1 )) "Registry Mirrors"

echo
echo "== 验证拉取 =="
if docker pull nginx:alpine; then
  echo
  echo "通了。接着："
  echo "  cd /opt/lexify && sudo docker compose up -d --build"
else
  echo
  echo "仍然失败——注意 BuildKit 的一个坑：buildx 用的是 docker-container 驱动时"
  echo "会忽略 daemon.json 的 registry-mirrors。先确认驱动："
  echo "  docker buildx ls"
  echo "若驱动不是 docker，先手工把基础镜像拉下来再重新 tag，构建时就不会再访问 registry："
  echo "  sudo docker pull ${OK[0]}/library/nginx:alpine"
  echo "  sudo docker tag  ${OK[0]}/library/nginx:alpine nginx:alpine"
  exit 1
fi
