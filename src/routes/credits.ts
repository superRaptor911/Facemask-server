import { Router } from "express";
import { prisma } from "../lib/prisma.js";
import { requireAuth } from "../middleware/require-auth.js";

export const creditsRouter: Router = Router();

creditsRouter.get("/", requireAuth, async (req, res) => {
  const transactions = await prisma.creditTransaction.findMany({
    where: { userId: req.user!.id },
    orderBy: { createdAt: "desc" },
    take: 50,
  });
  res.json({ balance: req.user!.creditBalance, transactions });
});
