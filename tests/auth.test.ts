import request from "supertest";
import { afterAll, describe, expect, it, vi } from "vitest";
import { createApp } from "../src/app.js";
import { prisma } from "../src/lib/prisma.js";
import { verifyGoogleIdToken } from "../src/services/google-auth.js";

vi.mock("../src/services/google-auth.js", () => ({
  verifyGoogleIdToken: vi.fn(),
}));

const mockVerify = vi.mocked(verifyGoogleIdToken);
const app = createApp();
const prefix = `vitest-auth-${process.pid}-${Date.now()}`;

afterAll(async () => {
  await prisma.user.deleteMany({ where: { providerSubjectId: { startsWith: prefix } } });
  await prisma.$disconnect();
});

async function signIn(tag: string) {
  mockVerify.mockResolvedValueOnce({
    sub: `${prefix}-${tag}`,
    email: `${tag}@example.com`,
    name: "Tester",
  });
  return request(app).post("/api/v1/auth/google").send({ idToken: "mocked" });
}

describe("POST /api/v1/auth/google", () => {
  it("creates a user and returns a session token", async () => {
    const res = await signIn("signup");

    expect(res.status).toBe(200);
    expect(res.body.token).toBeTypeOf("string");
    expect(res.body.token.length).toBeGreaterThan(30);
    expect(res.body.user.email).toBe("signup@example.com");
    expect(res.body.user.creditBalance).toBe(0);
    expect(res.body.user.providerSubjectId).toBeUndefined();
  });

  it("returns the same user on repeated sign-in", async () => {
    const first = await signIn("repeat");
    const second = await signIn("repeat");

    expect(second.body.user.id).toBe(first.body.user.id);
  });

  it("rejects an invalid Google token with 401", async () => {
    mockVerify.mockRejectedValueOnce(new Error("bad token"));
    const res = await request(app).post("/api/v1/auth/google").send({ idToken: "bad" });

    expect(res.status).toBe(401);
    expect(res.body.error).toBe("Invalid Google ID token");
  });

  it("rejects a missing idToken with 400", async () => {
    const res = await request(app).post("/api/v1/auth/google").send({});

    expect(res.status).toBe(400);
  });
});

describe("session auth", () => {
  it("grants access to /me with a valid token, rejects without", async () => {
    const { body } = await signIn("me");

    const ok = await request(app).get("/api/v1/me").set("Authorization", `Bearer ${body.token}`);
    expect(ok.status).toBe(200);
    expect(ok.body.id).toBe(body.user.id);

    const missing = await request(app).get("/api/v1/me");
    expect(missing.status).toBe(401);

    const garbage = await request(app).get("/api/v1/me").set("Authorization", "Bearer garbage");
    expect(garbage.status).toBe(401);
  });

  it("logout revokes the session", async () => {
    const { body } = await signIn("logout");

    const out = await request(app)
      .post("/api/v1/auth/logout")
      .set("Authorization", `Bearer ${body.token}`);
    expect(out.status).toBe(204);

    const after = await request(app).get("/api/v1/me").set("Authorization", `Bearer ${body.token}`);
    expect(after.status).toBe(401);
  });
});
