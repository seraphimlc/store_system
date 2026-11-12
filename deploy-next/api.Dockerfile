# Built by the developer's local Docker engine; deployment targets do not build.
FROM node:22.21.1-slim
WORKDIR /app
RUN npm install --global pnpm@12.18.0
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/server/package.json apps/server/package.json
RUN pnpm install --prod --frozen-lockfile --filter @store/server --ignore-scripts
COPY apps/server/dist apps/server/dist
COPY apps/worker/dist apps/worker/dist
COPY zone-evidence /app/zone-evidence
ENV NODE_ENV=production NEXT_HOST=0.0.0.0 NEXT_ZONE_DATA_DIRECTORY=/app/zone-evidence
USER node
EXPOSE 3000
CMD ["node", "apps/server/dist/apps/server/src/main.js"]
