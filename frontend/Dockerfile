# Two-stage build: compile with Node, ship only the static files under nginx.
#
# The result is an image with no Node runtime and no node_modules — just nginx
# and a few hundred kB of assets.

# --- build stage ---
FROM node:20-alpine AS build
WORKDIR /app

# The backend origin has to be known at BUILD time, not run time: Vite inlines
# import.meta.env values into the bundle, so these cannot be supplied as
# container env vars later. Hence ARG (build input) promoted to ENV so the
# `npm run build` below can see them.
#
# The localhost defaults suit docker-compose; a real deploy passes
# --build-arg VITE_API_BASE_URL=https://api.example.com
ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_WS_BASE_URL=ws://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_WS_BASE_URL=$VITE_WS_BASE_URL

# package.json copied and installed BEFORE the source, so Docker can reuse the
# cached dependency layer whenever only application code has changed.
COPY package.json ./
RUN npm install
COPY . .
# Runs tsc --noEmit first (see package.json), so a type error fails the image
# build rather than shipping.
RUN npm run build

# --- serve stage ---
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Only the built output crosses from the build stage — no source, no
# node_modules.
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
# daemon off, because a container needs its main process to stay in the
# foreground; nginx would otherwise background itself and the container would
# exit immediately.
CMD ["nginx", "-g", "daemon off;"]
