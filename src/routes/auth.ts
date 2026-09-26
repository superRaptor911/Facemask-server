import { Router } from "express";
import { prisma } from "../lib/prisma.js";
import { HttpError } from "../errors/http-error.js";
import { toPublicUser } from "../lib/serializers.js";
import { verifyGoogleIdToken, type GoogleIdentity } from "../services/google-auth.js";
import { createSession } from "../services/sessions.js";
import { requireAuth } from "../middleware/require-auth.js";

export const authRouter: Router = Router();

authRouter.post("/google", async (req, res) => {
  const { idToken } = (req.body ?? {}) as { idToken?: unknown };
  if (typeof idToken !== "string" || idToken.length === 0) {
    throw new HttpError(400, "idToken is required");
  }

  let identity: GoogleIdentity;
  try {
    identity = await verifyGoogleIdToken(idToken);
  } catch {
    throw new HttpError(401, "Invalid Google ID token");
  }

  const profile = {
    email: identity.email ?? null,
    name: identity.name ?? null,
    pictureUrl: identity.picture ?? null,
  };
  const user = await prisma.user.upsert({
    where: {
      provider_providerSubjectId: { provider: "google", providerSubjectId: identity.sub },
    },
    create: { provider: "google", providerSubjectId: identity.sub, ...profile },
    update: profile,
  });

  const token = await createSession(user.id);
  res.json({ token, user: toPublicUser(user) });
});

authRouter.post("/logout", requireAuth, async (req, res) => {
  await prisma.session.delete({ where: { id: req.session!.id } });
  res.status(204).end();
});
