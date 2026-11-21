ARG NODE_IMAGE=node:24-alpine
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.27-alpine
FROM ${NODE_IMAGE} AS assets
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/src ./src
COPY web/index.html web/tsconfig.json web/vite.config.ts ./
RUN npm run build

FROM ${NGINX_IMAGE}
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=assets /app/dist/ /usr/share/nginx/html/
