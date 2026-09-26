import { Prisma } from "../generated/prisma/client.js";
import { prisma } from "../lib/prisma.js";
import { HttpError } from "../errors/http-error.js";

function isUniqueViolation(err: unknown): boolean {
  return err instanceof Prisma.PrismaClientKnownRequestError && err.code === "P2002";
}

export interface GrantResult {
  credits: number;
  balance: number;
  alreadyGranted: boolean;
}

// Grants credits for a verified Play purchase. Idempotent on purchaseToken:
// replays return the current state without granting twice.
export async function grantPurchase(
  userId: string,
  productId: string,
  purchaseToken: string,
  orderId?: string,
): Promise<GrantResult> {
  const product = await prisma.creditProduct.findUnique({ where: { productId } });
  if (!product || !product.active) {
    throw new HttpError(422, "Unknown or inactive product");
  }

  try {
    return await prisma.$transaction(async (tx) => {
      const purchase = await tx.purchase.create({
        data: {
          userId,
          productId,
          purchaseToken,
          orderId: orderId ?? null,
          credits: product.credits,
        },
      });
      await tx.creditTransaction.create({
        data: { userId, amount: product.credits, type: "PURCHASE", purchaseId: purchase.id },
      });
      const user = await tx.user.update({
        where: { id: userId },
        data: { creditBalance: { increment: product.credits } },
      });
      return { credits: product.credits, balance: user.creditBalance, alreadyGranted: false };
    });
  } catch (err) {
    if (isUniqueViolation(err)) {
      const user = await prisma.user.findUniqueOrThrow({ where: { id: userId } });
      return { credits: product.credits, balance: user.creditBalance, alreadyGranted: true };
    }
    throw err;
  }
}

export interface SpendResult {
  balance: number;
  duplicate: boolean;
}

// Server-authoritative spend: atomic balance guard + ledger entry in one
// transaction. Pass an idempotencyKey so client retries can't double-spend.
export async function spendCredits(
  userId: string,
  amount: number,
  opts: { idempotencyKey?: string; note?: string } = {},
): Promise<SpendResult> {
  if (!Number.isInteger(amount) || amount <= 0) {
    throw new HttpError(400, "Spend amount must be a positive integer");
  }

  try {
    return await prisma.$transaction(async (tx) => {
      const updated = await tx.user.updateMany({
        where: { id: userId, creditBalance: { gte: amount } },
        data: { creditBalance: { decrement: amount } },
      });
      if (updated.count === 0) {
        throw new HttpError(402, "Insufficient credits");
      }
      await tx.creditTransaction.create({
        data: {
          userId,
          amount: -amount,
          type: "SPEND",
          idempotencyKey: opts.idempotencyKey ?? null,
          note: opts.note ?? null,
        },
      });
      const user = await tx.user.findUniqueOrThrow({ where: { id: userId } });
      return { balance: user.creditBalance, duplicate: false };
    });
  } catch (err) {
    if (isUniqueViolation(err) && opts.idempotencyKey) {
      // Retried spend: the transaction rolled back, the original one stands.
      const user = await prisma.user.findUniqueOrThrow({ where: { id: userId } });
      return { balance: user.creditBalance, duplicate: true };
    }
    throw err;
  }
}
