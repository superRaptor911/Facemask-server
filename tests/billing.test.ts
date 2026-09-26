import request from "supertest";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { createApp } from "../src/app.js";
import { prisma } from "../src/lib/prisma.js";
import { createSession } from "../src/services/sessions.js";
import { getProductPurchase } from "../src/services/play-billing.js";

vi.mock("../src/services/play-billing.js", () => ({
  PURCHASE_STATE_PURCHASED: 0,
  getProductPurchase: vi.fn(),
  listVoidedPurchases: vi.fn(),
}));

const mockGetPurchase = vi.mocked(getProductPurchase);
const app = createApp();
const prefix = `vitest-billing-${process.pid}-${Date.now()}`;
const productId = `${prefix}-pack100`;

let token: string;
let userId: string;

beforeAll(async () => {
  await prisma.creditProduct.create({ data: { productId, credits: 100 } });
  const user = await prisma.user.create({
    data: { provider: "google", providerSubjectId: `${prefix}-buyer` },
  });
  userId = user.id;
  token = await createSession(user.id);
});

afterAll(async () => {
  await prisma.creditTransaction.deleteMany({ where: { userId } });
  await prisma.purchase.deleteMany({ where: { userId } });
  await prisma.user.deleteMany({ where: { id: userId } });
  await prisma.creditProduct.deleteMany({ where: { productId: { startsWith: prefix } } });
  await prisma.$disconnect();
});

function verify(body: object) {
  return request(app)
    .post("/api/v1/billing/verify")
    .set("Authorization", `Bearer ${token}`)
    .send(body);
}

describe("POST /api/v1/billing/verify", () => {
  it("grants credits for a verified purchase", async () => {
    mockGetPurchase.mockResolvedValueOnce({ purchaseState: 0, orderId: "GPA.1234-1" });
    const res = await verify({ productId, purchaseToken: `${prefix}-token-1` });

    expect(res.status).toBe(201);
    expect(res.body).toEqual({ credits: 100, balance: 100, alreadyGranted: false });
  });

  it("is idempotent on purchase token replay", async () => {
    mockGetPurchase.mockResolvedValue({ purchaseState: 0, orderId: "GPA.1234-2" });
    const first = await verify({ productId, purchaseToken: `${prefix}-token-2` });
    const replay = await verify({ productId, purchaseToken: `${prefix}-token-2` });

    expect(first.status).toBe(201);
    expect(replay.status).toBe(200);
    expect(replay.body.alreadyGranted).toBe(true);
    expect(replay.body.balance).toBe(first.body.balance);
  });

  it("rejects a purchase that is not in purchased state", async () => {
    mockGetPurchase.mockResolvedValueOnce({ purchaseState: 2 });
    const res = await verify({ productId, purchaseToken: `${prefix}-pending` });

    expect(res.status).toBe(400);
    expect(res.body.error).toBe("Purchase is not in a purchased state");
  });

  it("rejects a token Google cannot verify", async () => {
    mockGetPurchase.mockRejectedValueOnce(new Error("404 from Play"));
    const res = await verify({ productId, purchaseToken: `${prefix}-bogus` });

    expect(res.status).toBe(400);
    expect(res.body.error).toBe("Purchase could not be verified with Google Play");
  });

  it("rejects an unknown product with 422", async () => {
    mockGetPurchase.mockResolvedValueOnce({ purchaseState: 0 });
    const res = await verify({ productId: `${prefix}-nonexistent`, purchaseToken: `${prefix}-t` });

    expect(res.status).toBe(422);
  });

  it("requires auth", async () => {
    const res = await request(app)
      .post("/api/v1/billing/verify")
      .send({ productId, purchaseToken: "t" });

    expect(res.status).toBe(401);
  });
});
