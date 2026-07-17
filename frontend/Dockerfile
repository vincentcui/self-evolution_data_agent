# ── Stage 1: 构建静态产物 ──
FROM node:20-alpine AS build

WORKDIR /app

# 先 COPY 依赖清单 (lockfile + package.json) 利用 layer 缓存
COPY package.json package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com

# 再 COPY 源码构建
COPY . .
RUN npm run build

# ── Stage 2: nginx 托管 ──
# 钉死 minor 版本: nginx:alpine 是 rolling tag, 2026-07-17 滚到 1.31.3 (Alpine 15.2/glibc),
# 在 CentOS 7 内核 3.10 上 pwrite /run/nginx.pid 报 EPERM 起不来. 1.27-alpine (Alpine 14.2) 验证可用.
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
