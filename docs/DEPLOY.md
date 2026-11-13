# 部署到服务器

站点是纯静态的：一个 `nginx:alpine` 容器装下 `web/` 就完事，没有 Node、没有后端、没有数据库。
下面按「把代码送上去 → 起容器 → 域名与 HTTPS → 日常运维」四步走，适合 Ubuntu / Debian 系。

---

## 0. 服务器准备

```bash
# Docker（官方脚本，装完顺手把当前用户加进 docker 组）
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER    # 重新登录生效
docker compose version           # 需要 v2，即 docker compose 而不是 docker-compose
```

只开 80 / 443。如果前面还要挂宿主机的 nginx 或 Caddy，8080 只在环回口用，不用放行。

```bash
sudo ufw allow 80,443/tcp
```

### 0.1 拉不到镜像（国内机器的必踩项）

`docker compose up -d --build` 报：

```
failed to resolve source metadata for docker.io/library/nginx:alpine:
  dial tcp 157.240.6.35:443: connect: connection refused
```

**这是 DNS 污染，不是防火墙。** `157.240.x.x` 是 Facebook 的网段，`registry-1.docker.io` 被解析到了那里，所以连不上——放行 443、关 ufw 都没用。改镜像加速：

```bash
sudo mkdir -p /etc/docker
sudo cat /etc/docker/daemon.json 2>/dev/null   # 先看有没有旧内容，别直接覆盖
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": [
    "https://mirror.ccs.tencentyun.com",
    "https://docker.m.daocloud.io",
    "https://docker.1ms.run"
  ]
}
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker

docker info | grep -A4 "Registry Mirrors"     # 确认列表已生效
docker pull nginx:alpine                      # 能 pull 完就通了
```

源的选择（2026-11 实测状态）：

| 源 | 地址 | 说明 |
|---|---|---|
| 腾讯云内网 | `https://mirror.ccs.tencentyun.com` | **腾讯云 CVM / 轻量首选**，走内网不计流量，公网访问无效 |
| 阿里云内网 | `https://xxx.mirror.aliyuncs.com` | 阿里云 ECS 用，地址按账号在控制台取；非 ECS 返回 403 |
| DaoCloud | `https://docker.m.daocloud.io` | 社区维护，跨云可用，适合兜底 |
| 1ms / 1Panel | `https://docker.1ms.run`、`https://docker.1panel.live` | 社区源，时好时坏 |
| ~~中科大 / 网易~~ | ~~`docker.mirrors.ustc.edu.cn`~~、~~`hub-mirror.c.163.com`~~ | **已停服**，配了只会拖慢，见到就删 |

三个都拉不动就走 §1.B 的离线镜像，那条路完全不碰 registry。也可以直接跳过 Docker 用系统 nginx：

```bash
sudo apt install -y nginx
sudo rsync -a --delete web/ /usr/share/nginx/html/
sudo cp docker/nginx.conf /etc/nginx/conf.d/lexify.conf   # 里面有 map，得放 http 块内
```

纯静态站这样跑最省事，代价是没有 `restart: unless-stopped` 那种自愈能力。

**探测哪个源真的可用**（比逐个手改快）：

```bash
sudo bash scripts/fix-docker-registry.sh --dry   # 只探测，打印每个源的状态
sudo bash scripts/fix-docker-registry.sh        # 探测 → 合并写入 daemon.json → 重启 → 试拉
```

脚本会保留 `daemon.json` 里已有的键（`runtimes` 之类），只覆盖 `registry-mirrors`；一个都探测不到时会直接打印离线镜像的做法然后退出。

**BuildKit 的坑**：`daemon.json` 的 mirrors 只对默认 `docker` 驱动生效。如果 `docker buildx ls` 显示的驱动是 `docker-container`，构建时照样直连 Docker Hub。这种情况下先手工把基础镜像拉下来再改名，构建时本地已有就不会再访问 registry：

```bash
sudo docker pull docker.m.daocloud.io/library/nginx:alpine
sudo docker tag  docker.m.daocloud.io/library/nginx:alpine nginx:alpine
sudo docker compose up -d --build
```

构建日志里 `current commit information was not captured` 那条 WARNING 可以忽略——`/opt/lexify` 是打包传上去的、不是 git clone，没有 `.git` 而已。

### 0.2 `permission denied ... /var/run/docker.sock`

当前用户不在 `docker` 组里。`usermod` 之后**必须重新登录 SSH**（`newgrp docker` 只对当前 shell 生效，换个终端又没了）：

```bash
sudo usermod -aG docker $USER
# 断开重连，然后
groups | grep docker
docker ps
```

没耐心重登就继续用 `sudo docker ...`，效果一样。

---

## 1. 把代码送到服务器

### A. 服务器能连 GitHub（最省事）

```bash
sudo git clone https://github.com/bytemineur/lexify.git /opt/lexify
cd /opt/lexify
```

国内机器连 GitHub 常年抽风，可以先在本地打好包再传（**注意 `web/data/*.js` 就是数据本体，必须一起带上**）：

```bash
# 本地
tar czf lexify.tgz --exclude=.git .
scp lexify.tgz root@你的服务器:/opt/

# 服务器
sudo mkdir -p /opt/lexify && sudo tar xzf /opt/lexify.tgz -C /opt/lexify
```

### B. 服务器连不上外网 / 不想装构建环境

把本机已经跑通的镜像直接搬过去，服务器只负责运行：

```bash
# 本地：把基础镜像一起打进去。两个 tag 共用同一批层，压缩后体积不变（约 28 MB）
docker save lexify:latest nginx:alpine | gzip -1 > lexify-image.tgz
scp lexify-image.tgz ubuntu@你的服务器:/opt/

# 服务器
gunzip -c /opt/lexify-image.tgz | sudo docker load
docker images | grep -E "lexify|nginx"     # lexify 111 MB、nginx 94.5 MB
cd /opt/lexify && sudo docker compose up -d --build
```

**为什么要把 `nginx:alpine` 也带上**：`docker build` 只有在缺基础镜像时才联网，`FROM` 在本地找得到，`COPY web/` 是纯本地操作，整条构建链路不碰 registry，所以带了基础镜像后 `--build` 可以照常用。

只打了 `lexify:latest` 的话，必须加 `--no-build`——否则 compose 见 `build: .` 会再去拉一次 `nginx:alpine`，又卡在 DNS 上。

这种方式服务器上没有源码，以后更新数据得重新 `docker save` 一次；好处是构建环境（GPU、大磁盘）不用跟着上服务器，而且完全绕开 registry。

---

## 2. 起容器

仓库自带的 `docker-compose.yml` 可以直接用，生产唯一要改的是端口——**让容器只监听环回口，对外交给宿主机反代**：

```yaml
services:
  lexify:
    build: .
    image: lexify:latest
    container_name: lexify
    ports:
      - "127.0.0.1:8080:80"      # 只在本机可达，外网进不来
    restart: unless-stopped
```

```bash
cd /opt/lexify
docker compose up -d --build
docker ps --filter name=lexify      # 等到 (healthy)
curl -sI http://127.0.0.1:8080/ | head -1
```

容器自带 `HEALTHCHECK`（每 30 秒 `wget --spider` 一次根路径），`restart: unless-stopped` 让它在崩溃或宿主重启后自己回来。

如果这台机器只跑 lexify、没有别的站点，也可以直接 `-p 80:80`，跳过第 3 步的反代，但 HTTPS 就得自己想办法了——不建议。

---

## 3. 域名与 HTTPS

### 方案 A：宿主机 nginx 反代（服务器上已经有 nginx 时用这个）

```nginx
# /etc/nginx/conf.d/lexify.conf
server {
    listen 80;
    server_name lexify.你的域名.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name lexify.你的域名.com;

    ssl_certificate     /etc/letsencrypt/live/lexify.你的域名.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/lexify.你的域名.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo certbot --nginx -d lexify.你的域名.com
sudo nginx -t && sudo systemctl reload nginx
```

**两个坑，都在这一层踩：**

1. **不要再开 `gzip on`。** 容器里的 nginx 已经把 7.3 MB 的 `hearing.js` 压到 1.06 MB，反代再压一次是纯浪费 CPU，个别客户端还会因为双重编码解不出来。宿主机这层保持默认（nginx 默认 `gzip off`）就好。
2. **不要覆盖缓存头。** `data/` 必须 `no-cache`（数据每天重导），`css/js` 1 小时，图片 7 天——这些都由容器里的 `docker/nginx.conf` 用 `map` 统一给，反代加上自己的 `add_header Cache-Control` 反而会把它们顶掉。

### 方案 B：Caddy 反代（自动签证书，配置最短）

```caddyfile
lexify.你的域名.com {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy 默认会自己 gzip，这时把容器里的 `gzip on` 关掉更干净（`docker/nginx.conf` 改一行），或者给反代加 `encode zstd` 之外的策略不必强求——重复压缩只是浪费，不影响正确性。

### 方案 C：直接上静态托管

不含后端逻辑，`web/` 整个目录可以直接丢给 Cloudflare Pages / Vercel / GitHub Pages / 对象存储。
代价是丢掉容器里的缓存策略与安全头，需要在对端补（Cloudflare 有现成的 Header Rules）。

---

## 4. 备案

页脚挂的是 `粤ICP备2026168850号`。

- **服务器在中国大陆**：80 / 443 必须先完成 ICP 备案，且备案主体、接入商要和这台机器一致，否则会被运营商拦截。备案号挂在页面上才合规。
- **服务器在境外（含香港）**：不需要备案，但把大陆备案号挂在境外站点上是不合规的，应当去掉页脚那一行。

改页脚就是 `web/index.html` 里 `<div class="foot-legal">` 那两行，改完重新 `docker compose up -d --build`。

---

## 5. 数据更新

数据是用 `COPY web/` 打进镜像的，**`docker compose restart` 不会重新读宿主机的 `web/data`**（实测：改了数据 → restart → 还是旧内容）。

### A. 重建镜像（简单，约 10 秒）

```bash
git pull                       # 或者 rsync 上去新的 web/data/*.js
docker compose up -d --build
```

### B. 挂卷（服务器不用重建，推荐长期用）

`docker-compose.yml` 里取消注释：

```yaml
volumes:
  - ./web:/usr/share/nginx/html:ro
```

之后更新就只是「本地 `python scripts/build_data.py` → `rsync -av web/ 服务器:/opt/lexify/web/`」，刷新即生效，连 restart 都不用。

**注意挂 `./web` 整目录，不要只挂 `./web/data`**——只挂 data 的话，改了 `board.js` / `millennium.css` 不会生效。

本机重导数据：

```bash
python scripts/build_data.py        # 源库路径走环境变量或同级克隆，详见 README
```

---

## 6. 日常运维

```bash
docker logs -f --tail 100 lexify            # 访问日志（容器内是 nginx 默认 stdout）
docker inspect lexify --format '{{.State.Health.Status}}'
docker compose restart                      # 改完 nginx.conf 用这个，别用 kill
docker compose up -d --build                # 更新代码 / 数据
docker system prune -f                      # 清旧镜像，一个 lexify 约 109 MB
```

**改 `docker/nginx.conf` 之后**，`restart` 不够——配置是 `COPY` 进镜像的，同样要 `up -d --build`。想边改边试可以临时挂进去：

```bash
docker run --rm -v "$PWD/docker/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
  nginx:alpine nginx -t                     # 先验语法，再重建
```

---

## 7. 上线自检

部署完按这四条过一遍，都对就说明这层没问题：

```bash
# ① 首页 200
curl -sI https://lexify.你的域名.com/ | head -1

# ② gzip 生效（应看到 content-encoding: gzip，且传输量约 1 MB）
curl -sI --compressed https://lexify.你的域名.com/data/hearing.js | grep -i "content-encoding"

# ③ 安全头在（三条都要有）
curl -sI https://lexify.你的域名.com/data/hearing.js | grep -iE "x-content-type|x-frame|referrer"

# ④ 缓存分档对：data 是 no-cache，css/js 是 max-age=3600
curl -sI https://lexify.你的域名.com/data/overview.js | grep -i cache-control
curl -sI https://lexify.你的域名.com/style.css      | grep -i cache-control
```

浏览器里再确认一次三块看板能筛、能搜、能导出 CSV、详情弹窗能开——这四件事对了，站长这样就算上线完成。

---

## 8. 容量与性能参考

| 项目 | 数值 |
|---|---|
| 镜像大小 | 109 MB（`nginx:alpine` + 12 MB 静态文件） |
| 容器常驻内存 | 约 14 MB |
| 构建耗时 | 首次约 1 分钟（拉基础镜像），之后 10 秒 |
| 首屏数据量 | 开庭页 7.3 MB → gzip 后 1.06 MB |
| 单机并发 | 实测 578 QPS（本机环回 100 并发，0 错误） |

访问量真起来后再考虑：构建期预生成 `.gz` 开 `gzip_static`（省掉每次 180 ms 的现压），或者把数据按省份拆包只下发首屏那份。
现在这个量级，单核 1 G 内存的小机器就够。
