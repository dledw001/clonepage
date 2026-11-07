FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
COPY scripts/npm-ci.sh ./scripts/npm-ci.sh
RUN sh scripts/npm-ci.sh
COPY . .
RUN npm run build

FROM caddy:2-alpine

COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /usr/share/caddy