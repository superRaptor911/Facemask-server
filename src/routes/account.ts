import { Router } from "express";
import { prisma } from "../lib/prisma.js";
import { requireAuth } from "../middleware/require-auth.js";

export const accountRouter: Router = Router();

// Immediate hard delete (Play policy requires in-app account deletion).
// Sessions cascade; purchases and ledger rows are retained de-identified
// (userId set to null). Unspent credits are forfeited.
accountRouter.delete("/", requireAuth, async (req, res) => {
  await prisma.user.delete({ where: { id: req.user!.id } });
  res.status(204).end();
});
