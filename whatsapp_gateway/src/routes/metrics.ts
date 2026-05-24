/**
 * GET /metrics route — exposes in-memory counters as JSON.
 * No authentication required (internal observability endpoint).
 */

import type { FastifyInstance } from "fastify";
import { getMetrics } from "../observability/metrics.js";

/**
 * Registers the GET /metrics route.
 * Returns current counter values: inbound_forwarded, inbound_failed,
 * outbound_sent, outbound_failed, queue_depth.
 */
export async function metricsRoute(app: FastifyInstance): Promise<void> {
  app.get("/metrics", async (_request, reply) => {
    return reply.status(200).send(getMetrics());
  });
}
