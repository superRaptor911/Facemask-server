import type { NextFunction, Request, Response } from "express";
import { prisma } from "../lib/prisma.js";
import { HttpError } from "../errors/http-error.js";
import { hashToken, SESSION_TOUCH_INTERVAL_MS, SESSION_TTL_MS } from "../services/sessions.js";

export async function requireAuth(req: Request, _res: Response, next: NextFunction): Promise<void> {
  const header = req.headers.authorization;
  const token = header?.startsWith("Bearer ") ? header.slice("Bearer ".length) : undefined;
  if (!token) {
    throw new HttpError(401, "Missing bearer token");
  }

  const session = await prisma.session.findUnique({
    where: { tokenHash: hashToken(token) },
    include: { user: true },
  });
  if (!session || session.expiresAt.getTime() <= Date.now()) {
    throw new HttpError(401, "Invalid or expired session");
  }

  if (Date.now() - session.lastUsedAt.getTime() > SESSION_TOUCH_INTERVAL_MS) {
    await prisma.session.update({
      where: { id: session.id },
      data: { lastUsedAt: new Date(), expiresAt: new Date(Date.now() + SESSION_TTL_MS) },
    });
  }

  req.user = session.user;
  req.session = session;
  next();
}
