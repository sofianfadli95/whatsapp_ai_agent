import type { FastifyInstance } from "fastify";
import type { SessionManagerInterface } from "../baileys/session.js";

export interface HealthRouteOptions {
  sessionManager: SessionManagerInterface;
}

/**
 * Registers the GET /healthz and GET /readyz routes.
 *
 * - `/healthz`: Liveness probe. Returns 200 immediately with no dependency checks.
 *   No authentication required.
 * - `/readyz`: Readiness probe. Returns 200 only when the Baileys session is connected;
 *   otherwise returns 503 with `{ "status": "not_connected" }`.
 *   No authentication required.
 */
export async function healthRoute(
  app: FastifyInstance,
  opts: HealthRouteOptions,
): Promise<void> {
  const { sessionManager } = opts;

  app.get("/healthz", async (_request, reply) => {
    return reply.status(200).send({ status: "ok" });
  });

  app.get("/readyz", async (_request, reply) => {
    if (sessionManager.isConnected()) {
      return reply.status(200).send({ status: "ok" });
    }

    return reply.status(503).send({ status: "not_connected" });
  });
}
