import express, { type Express } from "express";
import { healthRouter } from "./routes/health.js";
import { v1Router } from "./routes/index.js";
import { notFound } from "./middleware/not-found.js";
import { errorHandler } from "./middleware/error-handler.js";

export function createApp(): Express {
  const app = express();

  app.disable("x-powered-by");
  app.use(express.json());
  app.use(express.urlencoded({ extended: true }));

  app.use("/health", healthRouter);
  app.use("/api/v1", v1Router);

  app.use(notFound);
  app.use(errorHandler);

  return app;
}
