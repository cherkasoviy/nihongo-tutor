# syntax=docker/dockerfile:1.7
# Builds the Mini App bundle and publishes it into the `miniapp_dist` volume served by Caddy.

FROM node:22-alpine AS build
WORKDIR /app
COPY miniapp/package.json miniapp/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY miniapp/ ./
RUN npm run build


FROM alpine:3.20 AS publish
COPY --from=build /app/dist /bundle
# Replace the volume contents atomically enough for a single-VM deploy.
CMD ["sh", "-c", "rm -rf /srv/miniapp/* /srv/miniapp/.[!.]* 2>/dev/null; cp -r /bundle/. /srv/miniapp/ && echo 'miniapp bundle published'"]
