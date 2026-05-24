# WhatsApp Gateway

A Node.js microservice that connects to WhatsApp Web via the [Baileys](https://github.com/WhiskeySockets/Baileys) library. It owns the WhatsApp session, handles QR-code pairing, persists auth state to disk, and exposes an authenticated internal HTTP API for the Python backend to send and receive messages.

## Prerequisites

- **Node.js** 20+ (Alpine-compatible for Docker)
- **pnpm** (enabled via `corepack enable`)
- A phone with WhatsApp installed (for QR pairing)

## Install

```bash
cd whatsapp_gateway
corepack enable
pnpm install
```

## Environment Variables

Create a `.env` file in the `whatsapp_gateway/` directory (one is already provided):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WHATSAPP_GATEWAY_INTERNAL_TOKEN` | Yes | — | Bearer token for authenticating `/qr`, `/send`, and outbound forwarding to the Python backend. Use the same value in the Python backend config. |
| `WHATSAPP_GATEWAY_AUTH_DIR` | Yes | `./auth_state` | Directory where Baileys persists session auth state. Must be writable. |
| `WHATSAPP_BACKEND_INBOUND_URL` | Yes | — | URL the gateway POSTs inbound events to (e.g. `http://localhost:8080/internal/whatsapp/inbound`). |
| `PORT` | No | `3001` | HTTP server port. |
| `HOST` | No | `0.0.0.0` | HTTP server bind address. |
| `LOG_LEVEL` | No | `info` | Pino log level (`trace`, `debug`, `info`, `warn`, `error`, `fatal`). |

## Running Locally (Development)

```bash
pnpm dev
```

This starts the gateway with `tsx watch` for hot-reload. The server listens on `http://localhost:3001`.

## Building and Running (Production)

```bash
pnpm build
pnpm start
```

## Running via Docker Compose

From the project root:

```bash
docker compose up -d whatsapp_gateway
```

The compose service:
- Builds the multi-stage Dockerfile (Node 20 Alpine, non-root user)
- Mounts a named volume `wa_gateway_state` at `/data/auth_state` for session persistence
- Exposes port `3001`
- Includes a healthcheck on `/healthz`

Check status:

```bash
docker compose ps whatsapp_gateway
```

View logs:

```bash
docker compose logs -f whatsapp_gateway
```

## Scanning the QR Code (Pairing)

When the gateway starts without persisted auth state (first run or after logout), it generates a QR code for WhatsApp Web pairing.

### 1. Fetch the QR code

```bash
curl -s -H "Authorization: Bearer YOUR_TOKEN" http://localhost:3001/qr | jq .
```

Response when pairing is pending:

```json
{
  "qr_data_url": "data:image/png;base64,iVBORw0KGgo..."
}
```

Response when already connected:

```json
{
  "status": "connected"
}
```

### 2. Render and scan

Copy the `qr_data_url` value and open it in a browser (paste the full `data:image/png;base64,...` string into the address bar), or use a script:

```bash
# Save QR as a PNG file
curl -s -H "Authorization: Bearer YOUR_TOKEN" http://localhost:3001/qr \
  | jq -r '.qr_data_url' \
  | sed 's/data:image\/png;base64,//' \
  | base64 -d > /tmp/qr.png

open /tmp/qr.png  # macOS
# xdg-open /tmp/qr.png  # Linux
```

### 3. Scan with your phone

Open WhatsApp on your phone → Settings → Linked Devices → Link a Device → scan the QR code.

### 4. Verify connection

```bash
curl -s http://localhost:3001/healthz
# {"status":"ok"}

curl -s http://localhost:3001/readyz
# {"status":"connected"}  (200 when paired)
# {"status":"not_connected"}  (503 when not paired)
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/healthz` | None | Liveness probe. Always returns 200. |
| `GET` | `/readyz` | None | Readiness probe. 200 if Baileys session is connected, 503 otherwise. |
| `GET` | `/qr` | Bearer | Returns QR data URL for pairing or connection status. |
| `POST` | `/send` | Bearer | Send a text message. Body: `{"to": "+628...", "body": "...", "idempotency_key": "..."}` |
| `GET` | `/metrics` | None | Operational counters (inbound/outbound success/failure, queue depth). |

## Backing Up Auth State

The auth state directory contains the Baileys session credentials. Back it up to avoid re-pairing after data loss.

### Local development

```bash
cp -r ./auth_state ./auth_state_backup_$(date +%Y%m%d)
```

### Docker

The named volume `wa_gateway_state` persists across container restarts. To create a backup:

```bash
docker run --rm \
  -v wa_gateway_state:/data \
  -v $(pwd)/backups:/backup \
  alpine tar czf /backup/auth_state_$(date +%Y%m%d).tar.gz -C /data .
```

To restore:

```bash
docker run --rm \
  -v wa_gateway_state:/data \
  -v $(pwd)/backups:/backup \
  alpine sh -c "rm -rf /data/* && tar xzf /backup/auth_state_YYYYMMDD.tar.gz -C /data"
```

## Logging Out and Re-Pairing

If you need to disconnect the session and pair with a different phone or re-pair the same phone:

### Option 1: Delete auth state (local)

```bash
rm -rf ./auth_state
pnpm dev
# Gateway will generate a new QR code
```

### Option 2: Delete auth state (Docker)

```bash
docker compose down whatsapp_gateway
docker volume rm whatsapp_sales_agent_wa_gateway_state
docker compose up -d whatsapp_gateway
# Fetch new QR from /qr endpoint
```

### Option 3: Unlink from phone

Open WhatsApp on your phone → Settings → Linked Devices → tap the linked device → Log Out.

The gateway will detect the `loggedOut` status, stop reconnecting, and expose a new QR code on the `/qr` endpoint.

## Reconnection Behavior

- **Non-logout disconnects** (network issues, server restarts): the gateway automatically reconnects without operator intervention.
- **Logged-out status**: the gateway stops reconnecting and waits for a new QR scan.

## Inbound Message Flow

1. Customer sends a WhatsApp message
2. Baileys emits a `messages.upsert` event
3. Gateway deduplicates by `(remoteJid, messageId)` via an in-memory LRU
4. Gateway normalizes the sender phone to E.164
5. Gateway POSTs the `InboundEvent` to `WHATSAPP_BACKEND_INBOUND_URL` with bearer auth
6. On backend failure: retries with 1s/2s/4s backoff (max 3 attempts)
7. On exhausted retries: persists to on-disk overflow queue (`{AUTH_DIR}/outbox/`)
8. Background scanner re-attempts overflow events every 30s

## Tests

```bash
# Unit tests
pnpm test

# Integration tests (uses FakeBaileysSocket, no real WhatsApp connection needed)
pnpm test:integration
```

## Project Structure

```
whatsapp_gateway/
├── src/
│   ├── index.ts              # Fastify app entry point
│   ├── baileys/
│   │   ├── session.ts        # SessionManager (useMultiFileAuthState)
│   │   └── inbound.ts        # messages.upsert handler
│   ├── forwarder/
│   │   └── forwarder.ts      # HTTP forwarder with retry + overflow
│   ├── middleware/
│   │   └── auth.ts           # Bearer-token middleware
│   ├── routes/
│   │   ├── send.ts           # POST /send
│   │   ├── qr.ts             # GET /qr
│   │   ├── health.ts         # GET /healthz, /readyz
│   │   └── metrics.ts        # GET /metrics
│   ├── observability/
│   │   ├── logger.ts         # Pino structured logging
│   │   └── metrics.ts        # Operational counters
│   └── utils/
│       └── phone.ts          # E.164 normalization
├── Dockerfile                # Multi-stage Node 20 Alpine
├── package.json
├── tsconfig.json
└── .env
```
