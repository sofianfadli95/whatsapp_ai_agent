import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Fastify, { type FastifyInstance } from "fastify";
import {
  getMetrics,
  resetMetrics,
  incrInboundForwarded,
  incrInboundFailed,
  incrOutboundSent,
  incrOutboundFailed,
  setQueueDepth,
} from "./metrics.js";
import { metricsRoute } from "../routes/metrics.js";

describe("metrics counters", () => {
  beforeEach(() => {
    resetMetrics();
  });

  it("starts with all counters at zero", () => {
    const m = getMetrics();
    expect(m).toEqual({
      inbound_forwarded: 0,
      inbound_failed: 0,
      outbound_sent: 0,
      outbound_failed: 0,
      queue_depth: 0,
    });
  });

  it("increments inbound_forwarded correctly", () => {
    incrInboundForwarded();
    incrInboundForwarded();
    expect(getMetrics().inbound_forwarded).toBe(2);
  });

  it("increments inbound_failed correctly", () => {
    incrInboundFailed();
    expect(getMetrics().inbound_failed).toBe(1);
  });

  it("increments outbound_sent correctly", () => {
    incrOutboundSent();
    incrOutboundSent();
    incrOutboundSent();
    expect(getMetrics().outbound_sent).toBe(3);
  });

  it("increments outbound_failed correctly", () => {
    incrOutboundFailed();
    expect(getMetrics().outbound_failed).toBe(1);
  });

  it("sets queue_depth to a specific value", () => {
    setQueueDepth(5);
    expect(getMetrics().queue_depth).toBe(5);
    setQueueDepth(0);
    expect(getMetrics().queue_depth).toBe(0);
  });

  it("returns a snapshot (not a reference to internal state)", () => {
    incrInboundForwarded();
    const snapshot = getMetrics();
    incrInboundForwarded();
    // The snapshot should not change after further increments
    expect(snapshot.inbound_forwarded).toBe(1);
    expect(getMetrics().inbound_forwarded).toBe(2);
  });

  it("resetMetrics sets all counters back to zero", () => {
    incrInboundForwarded();
    incrInboundFailed();
    incrOutboundSent();
    incrOutboundFailed();
    setQueueDepth(10);

    resetMetrics();

    expect(getMetrics()).toEqual({
      inbound_forwarded: 0,
      inbound_failed: 0,
      outbound_sent: 0,
      outbound_failed: 0,
      queue_depth: 0,
    });
  });
});

describe("GET /metrics endpoint", () => {
  let app: FastifyInstance;

  beforeEach(async () => {
    resetMetrics();
    app = Fastify();
    await app.register(metricsRoute);
    await app.ready();
  });

  afterEach(async () => {
    await app.close();
  });

  it("returns all counters as JSON with correct shape", async () => {
    const response = await app.inject({
      method: "GET",
      url: "/metrics",
    });

    expect(response.statusCode).toBe(200);
    const body = response.json();
    expect(body).toHaveProperty("inbound_forwarded");
    expect(body).toHaveProperty("inbound_failed");
    expect(body).toHaveProperty("outbound_sent");
    expect(body).toHaveProperty("outbound_failed");
    expect(body).toHaveProperty("queue_depth");
  });

  it("returns non-zero values after operations", async () => {
    incrInboundForwarded();
    incrOutboundSent();
    setQueueDepth(3);

    const response = await app.inject({
      method: "GET",
      url: "/metrics",
    });

    expect(response.statusCode).toBe(200);
    const body = response.json();
    expect(body.inbound_forwarded).toBe(1);
    expect(body.outbound_sent).toBe(1);
    expect(body.queue_depth).toBe(3);
  });

  it("does not require authentication", async () => {
    // No auth header provided — should still succeed
    const response = await app.inject({
      method: "GET",
      url: "/metrics",
    });

    expect(response.statusCode).toBe(200);
  });
});
