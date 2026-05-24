import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Fastify, { type FastifyInstance } from "fastify";
import { qrRoute } from "./qr.js";
import type { SessionManagerInterface } from "../baileys/session.js";

const TEST_TOKEN = "test-secret-token-12345";

/**
 * Fake SessionManager for testing different session states.
 */
class FakeSessionManager implements SessionManagerInterface {
  private _connected = false;
  private _qr: string | null = null;

  getSocket() {
    return null;
  }

  isConnected(): boolean {
    return this._connected;
  }

  currentQR(): string | null {
    return this._qr;
  }

  async start(): Promise<void> {
    // no-op for tests
  }

  stop(): void {
    // no-op for tests
  }

  // Test helpers
  setConnected(connected: boolean): void {
    this._connected = connected;
  }

  setQR(qr: string | null): void {
    this._qr = qr;
  }
}

describe("GET /qr", () => {
  let app: FastifyInstance;
  let fakeSession: FakeSessionManager;

  beforeEach(async () => {
    // Set the expected token in the environment
    process.env.WHATSAPP_GATEWAY_INTERNAL_TOKEN = TEST_TOKEN;

    fakeSession = new FakeSessionManager();

    app = Fastify();
    await app.register(qrRoute, { sessionManager: fakeSession });
    await app.ready();
  });

  afterEach(async () => {
    await app.close();
    delete process.env.WHATSAPP_GATEWAY_INTERNAL_TOKEN;
  });

  it("returns 401 when bearer token is missing", async () => {
    fakeSession.setQR("some-qr-data");

    const response = await app.inject({
      method: "GET",
      url: "/qr",
    });

    expect(response.statusCode).toBe(401);
    const body = response.json();
    expect(body.error).toBeDefined();
  });

  it("returns 401 when bearer token is wrong", async () => {
    fakeSession.setQR("some-qr-data");

    const response = await app.inject({
      method: "GET",
      url: "/qr",
      headers: {
        authorization: "Bearer wrong-token",
      },
    });

    expect(response.statusCode).toBe(401);
    const body = response.json();
    expect(body.error).toBeDefined();
  });

  it("returns 401 when authorization header has wrong format", async () => {
    fakeSession.setQR("some-qr-data");

    const response = await app.inject({
      method: "GET",
      url: "/qr",
      headers: {
        authorization: "Basic some-credentials",
      },
    });

    expect(response.statusCode).toBe(401);
  });

  it("returns 200 with qr_data_url when session is in QR-pending state", async () => {
    fakeSession.setConnected(false);
    fakeSession.setQR("test-qr-code-content");

    const response = await app.inject({
      method: "GET",
      url: "/qr",
      headers: {
        authorization: `Bearer ${TEST_TOKEN}`,
      },
    });

    expect(response.statusCode).toBe(200);
    const body = response.json();
    expect(body.qr_data_url).toBeDefined();
    expect(body.qr_data_url).toMatch(/^data:image\/png;base64,.+/);
    // Ensure it's non-empty base64 content
    const base64Part = body.qr_data_url.split(",")[1];
    expect(base64Part.length).toBeGreaterThan(0);
  });

  it("returns 200 with { status: 'connected' } when already paired", async () => {
    fakeSession.setConnected(true);
    fakeSession.setQR(null);

    const response = await app.inject({
      method: "GET",
      url: "/qr",
      headers: {
        authorization: `Bearer ${TEST_TOKEN}`,
      },
    });

    expect(response.statusCode).toBe(200);
    const body = response.json();
    expect(body.status).toBe("connected");
    expect(body.qr_data_url).toBeUndefined();
  });

  it("returns 503 when no QR code is available yet (initializing)", async () => {
    fakeSession.setConnected(false);
    fakeSession.setQR(null);

    const response = await app.inject({
      method: "GET",
      url: "/qr",
      headers: {
        authorization: `Bearer ${TEST_TOKEN}`,
      },
    });

    expect(response.statusCode).toBe(503);
    const body = response.json();
    expect(body.error).toBeDefined();
  });
});
