import { createHash, randomBytes } from "node:crypto";
import { prisma } from "../lib/prisma.js";

export const SESSION_TTL_MS = 90 * 24 * 60 * 60 * 1000; // 90 days, sliding
export const SESSION_TOUCH_INTERVAL_MS = 24 * 60 * 60 * 1000; // extend at most daily

export function hashToken(token: string): string {
  return createHash("sha256").update(token).digest("hex");
}

// Returns the raw bearer token; only its hash is stored.
export async function createSession(userId: string): Promise<string> {
  const token = randomBytes(32).toString("base64url");
  await prisma.session.create({
    data: {
      tokenHash: hashToken(token),
      userId,
      expiresAt: new Date(Date.now() + SESSION_TTL_MS),
    },
  });
  return token;
}
