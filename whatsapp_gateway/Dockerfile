# Stage 1: Build
FROM node:20-alpine AS build

WORKDIR /app

# Install pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

# Copy dependency manifests
COPY package.json pnpm-lock.yaml .npmrc ./

# Install all dependencies (including dev for tsc)
RUN pnpm install --frozen-lockfile

# Copy source and build
COPY tsconfig.json ./
COPY src ./src
RUN pnpm build

# Stage 2: Runtime
FROM node:20-alpine AS runtime

WORKDIR /app

# Install pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

# Copy dependency manifests and install production deps only
COPY package.json pnpm-lock.yaml .npmrc ./
RUN pnpm install --frozen-lockfile --prod

# Copy compiled output from build stage
COPY --from=build /app/dist ./dist

# Create volume mount point for auth state persistence
RUN mkdir -p /data/auth_state
VOLUME ["/data/auth_state"]

# Create non-root user and set ownership
RUN addgroup -S appgroup && adduser -S appuser -G appgroup \
    && chown -R appuser:appgroup /app /data/auth_state

USER appuser

# Expose port
ENV PORT=3001
EXPOSE ${PORT}

CMD ["node", "dist/index.js"]
