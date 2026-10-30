# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS dependencies

WORKDIR /workspace

COPY package.json package-lock.json ./
COPY packages/ui/package.json packages/ui/package.json
COPY apps/admin-console/package.json apps/admin-console/package.json
COPY apps/vm-screens/package.json apps/vm-screens/package.json
RUN --mount=type=cache,target=/root/.npm npm ci

COPY tsconfig.base.json vitest.config.ts vitest.setup.ts ./
COPY packages/ packages/
COPY apps/ apps/


FROM dependencies AS admin-build

ARG VITE_API_BASE_URL=
ARG VITE_SCREENS_ORIGIN=/screens
ARG VITE_VM_ROLE_BINDINGS=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_SCREENS_ORIGIN=${VITE_SCREENS_ORIGIN}
ENV VITE_VM_ROLE_BINDINGS=${VITE_VM_ROLE_BINDINGS}
ENV VITE_MOCK=false

RUN npm run build --workspace=@xenova/admin-console


FROM dependencies AS screens-build

ARG VITE_API_BASE_URL=
# Public Spring-backed app preview; dom0 kiosk builds use VITE_TELEMETRY=display and VITE_BASE_PATH=/.
ARG VITE_TELEMETRY=legacy-live
ARG VITE_BASE_PATH=/screens/
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_TELEMETRY=${VITE_TELEMETRY}
ENV VITE_BASE_PATH=${VITE_BASE_PATH}

RUN npm run build --workspace=@xenova/vm-screens


FROM nginx:1.27-alpine AS admin-console

COPY docker/nginx-spa.conf /etc/nginx/conf.d/default.conf
COPY --from=admin-build /workspace/apps/admin-console/dist/ /usr/share/nginx/html/

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1:8080/healthz || exit 1


FROM nginx:1.27-alpine AS vm-screens

COPY docker/nginx-spa.conf /etc/nginx/conf.d/default.conf
COPY --from=screens-build /workspace/apps/vm-screens/dist/ /usr/share/nginx/html/

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1:8080/healthz || exit 1
