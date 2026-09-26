import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApp } from "../src/app.js";
import { prisma } from "../src/lib/prisma.js";
import { createSession } from "../src/services/sessions.js";
import { grantPurchase, spendCredits } from "../src/services/credits.js";
import { HttpError } from "../src/errors/http-error.js";

const app = createApp();
const prefix = `vitest-credits-${process.pid}-${Date.now()}`;
const productId = `${prefix}-pack100`;

let userId: string;
let token: string;

beforeAll(async () => {
  await prisma.creditProduct.create({ data: { productId, credits: 100 } });
  const user = await prisma.user.create({
    data: { provider: "google", providerSubjectId: `${prefix}-spender` },
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

describe("credits service", () => {
  it("grants, spends, and keeps the ledger consistent", async () => {
    const grant = await grantPurchase(userId, productId, `${prefix}-token-1`);
    expect(grant).toEqual({ credits: 100, balance: 100, alreadyGranted: false });

    const spend = await spendCredits(userId, 30, { note: "test spend" });
    expect(spend.balance).toBe(70);

    const transactions = await prisma.creditTransaction.findMany({ where: { userId } });
    const sum = transactions.reduce((acc, t) => acc + t.amount, 0);
    const user = await prisma.user.findUniqueOrThrow({ where: { id: userId } });
    expect(sum).toBe(user.creditBalance);
  });

  it("rejects a spend beyond the balance with 402", async () => {
    await expect(spendCredits(userId, 1_000_000)).rejects.toMatchObject({ status: 402 });
    await expect(spendCredits(userId, 1_000_000)).rejects.toBeInstanceOf(HttpError);
  });

  it("rejects non-positive amounts", async () => {
    await expect(spendCredits(userId, 0)).rejects.toMatchObject({ status: 400 });
    await expect(spendCredits(userId, -5)).rejects.toMatchObject({ status: 400 });
    await expect(spendCredits(userId, 1.5)).rejects.toMatchObject({ status: 400 });
  });

  it("does not double-spend on idempotency-key replay", async () => {
    const key = `${prefix}-idem-1`;
    const first = await spendCredits(userId, 10, { idempotencyKey: key });
    const replay = await spendCredits(userId, 10, { idempotencyKey: key });

    expect(first.duplicate).toBe(false);
    expect(replay.duplicate).toBe(true);
    expect(replay.balance).toBe(first.balance);
  });

  it("allows only one of two racing spends when balance covers just one", async () => {
    const fresh = await prisma.user.create({
      data: { provider: "google", providerSubjectId: `${prefix}-racer`, creditBalance: 100 },
    });
    const results = await Promise.allSettled([
      spendCredits(fresh.id, 60),
      spendCredits(fresh.id, 60),
    ]);

    const fulfilled = results.filter((r) => r.status === "fulfilled");
    expect(fulfilled).toHaveLength(1);
    const user = await prisma.user.findUniqueOrThrow({ where: { id: fresh.id } });
    expect(user.creditBalance).toBe(40);

    await prisma.creditTransaction.deleteMany({ where: { userId: fresh.id } });
    await prisma.user.delete({ where: { id: fresh.id } });
  });
});

describe("GET /api/v1/credits", () => {
  it("returns balance and recent transactions", async () => {
    const res = await request(app).get("/api/v1/credits").set("Authorization", `Bearer ${token}`);

    expect(res.status).toBe(200);
    expect(res.body.balance).toBeTypeOf("number");
    expect(Array.isArray(res.body.transactions)).toBe(true);
    expect(res.body.transactions.length).toBeGreaterThan(0);
  });
});
