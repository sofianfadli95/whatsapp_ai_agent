import "dotenv/config";
import Fastify from "fastify";
import { getSessionManager } from "./baileys/session.js";
import { healthRoute } from "./routes/health.js";
import { qrRoute } from "./routes/qr.js";
import { sendRoute } from "./routes/send.js";
import { metricsRoute } from "./routes/metrics.js";
import { fastifyLoggerOptions } from "./observability/logger.js";

const PORT = parseInt(process.env.PORT ?? "3001", 10);
const HOST = process.env.HOST ?? "0.0.0.0";

async function main(): Promise<void> {
  const app = Fastify({
    logger: fastifyLoggerOptions,
    requestIdHeader: "x-request-id",
    genReqId: () => crypto.randomUUID(),
  });

  const sessionManager = getSessionManager();

  // Register routes
  await app.register(healthRoute, { sessionManager });
  await app.register(qrRoute, { sessionManager });
  await app.register(sendRoute, { sessionManager });
  await app.register(metricsRoute);

  // Start the server
  await app.listen({ port: PORT, host: HOST });

  // Start the Baileys session
  await sessionManager.start();

  app.log.info(`WhatsApp Gateway listening on ${HOST}:${PORT}`);
}

main().catch((err) => {
  console.error("Failed to start WhatsApp Gateway:", err);
  process.exit(1);
});
