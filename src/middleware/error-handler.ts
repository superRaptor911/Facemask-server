import type { NextFunction, Request, Response } from "express";
import { HttpError } from "../errors/http-error.js";
import { env } from "../config/env.js";

// Express 5 forwards rejected promises from async handlers here automatically.
export function errorHandler(
  err: unknown,
  _req: Request,
  res: Response,
  _next: NextFunction,
): void {
  if (err instanceof HttpError) {
    res.status(err.status).json({ error: err.message });
    return;
  }

  if (env.nodeEnv !== "test") {
    console.error(err);
  }
  res.status(500).json({ error: "Internal server error" });
}
