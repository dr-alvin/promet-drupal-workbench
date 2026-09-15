FROM backstopjs/backstopjs:6.3.25@sha256:020d8f17eaa1cd3f2165f3caa6b3f9c56c5311c2cdedc4897b8cf0484b07040f
USER root
COPY scripts/patch-backstop.js /tmp/patch-backstop.js
RUN node /tmp/patch-backstop.js
WORKDIR /opt/audit
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY scripts ./scripts
COPY engine_scripts ./engine_scripts
COPY backstop.config.js ./backstop.config.js
ENV NODE_PATH=/usr/local/lib/node_modules/backstopjs/node_modules:/usr/local/lib/node_modules
ENV HOME=/tmp/audit-home XDG_CACHE_HOME=/tmp/audit-cache
WORKDIR /work
ENTRYPOINT ["node", "/opt/audit/scripts/run.js"]
