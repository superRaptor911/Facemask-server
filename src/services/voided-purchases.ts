import { prisma } from "../lib/prisma.js";
import { listVoidedPurchases } from "./play-billing.js";

// Claws back credits for purchases refunded/voided on Google Play.
// Idempotent: a purchase is only ever voided once. Balances may go negative,
// which simply blocks further spending until the user tops up.
export async function processVoidedPurchases(): Promise<number> {
  let pageToken: string | undefined;
  let clawedBack = 0;

  do {
    const page = await listVoidedPurchases(pageToken);
    for (const voided of page.voidedPurchases) {
      const purchase = await prisma.purchase.findUnique({
        where: { purchaseToken: voided.purchaseToken },
      });
      if (!purchase || purchase.status === "VOIDED") {
        continue;
      }

      await prisma.$transaction(async (tx) => {
        await tx.purchase.update({
          where: { id: purchase.id },
          data: { status: "VOIDED", voidedAt: new Date() },
        });
        await tx.creditTransaction.create({
          data: {
            userId: purchase.userId,
            amount: -purchase.credits,
            type: "ADJUSTMENT",
            purchaseId: purchase.id,
            note: "Refunded/voided on Google Play",
          },
        });
        if (purchase.userId) {
          await tx.user.update({
            where: { id: purchase.userId },
            data: { creditBalance: { decrement: purchase.credits } },
          });
        }
      });
      clawedBack += 1;
    }
    pageToken = page.nextPageToken;
  } while (pageToken);

  return clawedBack;
}
