import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { prisma } from "../src/lib/prisma.js";
import { grantPurchase } from "../src/services/credits.js";
import { processVoidedPurchases } from "../src/services/voided-purchases.js";
import { listVoidedPurchases } from "../src/services/play-billing.js";

vi.mock("../src/services/play-billing.js", () => ({
  PURCHASE_STATE_PURCHASED: 0,
  getProductPurchase: vi.fn(),
  listVoidedPurchases: vi.fn(),
}));

const mockList = vi.mocked(listVoidedPurchases);
const prefix = `vitest-voided-${process.pid}-${Date.now()}`;
const productId = `${prefix}-pack100`;

let userId: string;

beforeAll(async () => {
  await prisma.creditProduct.create({ data: { productId, credits: 100 } });
  const user = await prisma.user.create({
    data: { provider: "google", providerSubjectId: `${prefix}-refunder` },
  });
  userId = user.id;
});

afterAll(async () => {
  await prisma.creditTransaction.deleteMany({ where: { userId } });
  await prisma.purchase.deleteMany({ where: { userId } });
  await prisma.user.deleteMany({ where: { id: userId } });
  await prisma.creditProduct.deleteMany({ where: { productId: { startsWith: prefix } } });
  await prisma.$disconnect();
});

describe("processVoidedPurchases", () => {
  it("claws back credits for voided purchases, exactly once", async () => {
    const purchaseToken = `${prefix}-token-1`;
    await grantPurchase(userId, productId, purchaseToken);
    await grantPurchase(userId, productId, `${prefix}-token-keep`);

    mockList.mockResolvedValue({
      voidedPurchases: [{ purchaseToken }, { purchaseToken: `${prefix}-unknown-token` }],
    });

    const firstRun = await processVoidedPurchases();
    expect(firstRun).toBe(1);

    const purchase = await prisma.purchase.findUniqueOrThrow({ where: { purchaseToken } });
    expect(purchase.status).toBe("VOIDED");
    expect(purchase.voidedAt).not.toBeNull();

    const user = await prisma.user.findUniqueOrThrow({ where: { id: userId } });
    expect(user.creditBalance).toBe(100); // 200 granted - 100 clawed back

    // Second run sees the same voided list — must be a no-op.
    const secondRun = await processVoidedPurchases();
    expect(secondRun).toBe(0);

    const untouched = await prisma.purchase.findUniqueOrThrow({
      where: { purchaseToken: `${prefix}-token-keep` },
    });
    expect(untouched.status).toBe("GRANTED");
  });

  it("follows pagination", async () => {
    const tokenA = `${prefix}-page-a`;
    const tokenB = `${prefix}-page-b`;
    await grantPurchase(userId, productId, tokenA);
    await grantPurchase(userId, productId, tokenB);

    mockList
      .mockResolvedValueOnce({
        voidedPurchases: [{ purchaseToken: tokenA }],
        nextPageToken: "page-2",
      })
      .mockResolvedValueOnce({ voidedPurchases: [{ purchaseToken: tokenB }] });

    const count = await processVoidedPurchases();
    expect(count).toBe(2);
    expect(mockList).toHaveBeenLastCalledWith("page-2");
  });
});
