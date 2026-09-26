import request from "supertest";
import { afterAll, describe, expect, it } from "vitest";
import { createApp } from "../src/app.js";
import { prisma } from "../src/lib/prisma.js";
import { createSession } from "../src/services/sessions.js";
import { grantPurchase } from "../src/services/credits.js";

const app = createApp();
const prefix = `vitest-account-${process.pid}-${Date.now()}`;
const productId = `${prefix}-pack100`;

afterAll(async () => {
  await prisma.creditTransaction.deleteMany({
    where: { purchase: { purchaseToken: { startsWith: prefix } } },
  });
  await prisma.purchase.deleteMany({ where: { purchaseToken: { startsWith: prefix } } });
  await prisma.creditProduct.deleteMany({ where: { productId: { startsWith: prefix } } });
  await prisma.user.deleteMany({ where: { providerSubjectId: { startsWith: prefix } } });
  await prisma.$disconnect();
});

describe("DELETE /api/v1/account", () => {
  it("hard-deletes the user but retains de-identified financial records", async () => {
    await prisma.creditProduct.create({ data: { productId, credits: 100 } });
    const user = await prisma.user.create({
      data: { provider: "google", providerSubjectId: `${prefix}-leaver`, email: "l@example.com" },
    });
    const token = await createSession(user.id);
    const purchaseToken = `${prefix}-token-1`;
    await grantPurchase(user.id, productId, purchaseToken);

    const res = await request(app)
      .delete("/api/v1/account")
      .set("Authorization", `Bearer ${token}`);
    expect(res.status).toBe(204);

    // User and sessions are gone.
    expect(await prisma.user.findUnique({ where: { id: user.id } })).toBeNull();
    expect(await prisma.session.findMany({ where: { userId: user.id } })).toHaveLength(0);

    // The old token no longer authenticates.
    const after = await request(app).get("/api/v1/me").set("Authorization", `Bearer ${token}`);
    expect(after.status).toBe(401);

    // Purchase + ledger rows remain, de-identified.
    const purchase = await prisma.purchase.findUniqueOrThrow({ where: { purchaseToken } });
    expect(purchase.userId).toBeNull();
    const transactions = await prisma.creditTransaction.findMany({
      where: { purchaseId: purchase.id },
    });
    expect(transactions).toHaveLength(1);
    expect(transactions[0]!.userId).toBeNull();
  });

  it("requires auth", async () => {
    const res = await request(app).delete("/api/v1/account");
    expect(res.status).toBe(401);
  });
});
