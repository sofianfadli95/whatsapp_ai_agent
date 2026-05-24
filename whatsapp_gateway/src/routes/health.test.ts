import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Fastify, { type FastifyInstance } from "fastify";
import { healthRoute } from "./health.js";
import type { SessionManagerInterface } from "../baileys/session.js";

/**
 * Fake SessionManager for testing connected/disconnected states.
 */
class FakeSessionManager implements SessionManagerInterface {
  private _connected = false;

  getSocket() {
    return null;
  }

  isConnected(): boolean {
    return this._connected;
  }

  currentQR(): string | null {
    return null;
  }

  async start(): Promise<void> {
    // no-op for tests
  }

  stop(): void {
    // no-op for tests
  }

  setConnected(connected: boolean): void {
    this._connected = connected;
  }
}

describe("GET /healthz", () => {
  let app: FastifyInstance;
  let fakeSession: FakeSessionManager;

  beforeEach(async () => {
    fakeSession = new FakeSessionManager();
    app = Fastify();
    await app.register(healthRoute, { sessionManager: fakeSession });
    await app.ready();
  });

  afterEach(async () => {
    await app.close();
  });

  it("returns 200 when session is connected", async () => {
    fakeSession.setConnected(true);

    const response = await app.inject({
      method: "GET",
      url: "/healthz",
    });

    expect(response.statusCode).toBe(200);
    const body = response.json();
    expect(body.status).toBe("ok");
  });

  it("returns 200 when session is disconnected (no dependency checks)", async () => {
    fakeSession.setConnected(false);

    const response = await app.inject({
      method: "GET",
      url: "/healthz",
    });

    expect(response.statusCode).toBe(200);
    const body = response.json();
    expect(body.status).toBe("ok");
  });

  it("does not require authentication", async () => {
    const response = await app.inject({
      method: "GET",
      url: "/healthz",
    });

    expect(response.statusCode).toBe(200);
  });
});

describe("GET /readyz", () => {
  let app: FastifyInstance;
  let fakeSession: FakeSessionManager;

  beforeEach(async () => {
    fakeSession = new FakeSessionManager();
    app = Fastify();
    await app.register(healthRoute, { sessionManager: fakeSession });
    await app.ready();
  });

  afterEach(async () => {
    await app.close();
  });

  it("returns 200 when session is connected", async () => {
    fakeSession.setConnected(true);

    const response = await app.inject({
      method: "GET",
      url: "/readyz",
    });

    expect(response.statusCode).toBe(200);
    const body = response.json();
    expect(body.status).toBe("ok");
  });

  it("returns 503 with not_connected when session is disconnected", async () => {
    fakeSession.setConnected(false);

    const response = await app.inject({
      method: "GET",
      url: "/readyz",
    });

    expect(response.statusCode).toBe(503);
    const body = response.json();
    expect(body.status).toBe("not_connected");
  });

  it("does not require authentication", async () => {
    fakeSession.setConnected(true);

    const response = await app.inject({
      method: "GET",
      url: "/readyz",
    });

    // Should succeed without any auth header
    expect(response.statusCode).toBe(200);
  });

  it("transitions from disconnected to connected correctly", async () => {
    fakeSession.setConnected(false);

    const disconnectedResponse = await app.inject({
      method: "GET",
      url: "/readyz",
    });
    expect(disconnectedResponse.statusCode).toBe(503);

    fakeSession.setConnected(true);

    const connectedResponse = await app.inject({
      method: "GET",
      url: "/readyz",
    });
    expect(connectedResponse.statusCode).toBe(200);
    expect(connectedResponse.json().status).toBe("ok");
  });
});
