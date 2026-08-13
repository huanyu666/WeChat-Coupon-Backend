FROM node:20-bookworm-slim

ENV NODE_ENV=production \
    PORT=18180 \
    MERCHANT_BENEFITS_UPSTREAM_TIMEOUT_MS=6000

WORKDIR /app
COPY server.js /app/server.js
COPY result-mapper.js /app/result-mapper.js
COPY runtime /app/runtime

EXPOSE 18180
CMD ["node", "server.js"]
