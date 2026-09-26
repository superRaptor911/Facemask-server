import express from "express";
import request from "supertest";
import { describe, expect, it } from "vitest";
import { createApp } from "../src/app.js";
import { HttpError } from "../src/errors/http-error.js";
import { errorHandler } from "../src/middleware/error-handler.js";

const app = createApp();

describe("GET /health", () => {
  it("returns ok with uptime and timestamp", async () => {
    const res = await request(app).get("/health");

    expect(res.status).toBe(200);
    expect(res.body.status).toBe("ok");
    expect(res.body.uptime).toBeTypeOf("number");
    expect(new Date(res.body.timestamp).getTime()).not.toBeNaN();
  });
});

describe("unknown routes", () => {
  it("returns a 404 with an error message", async () => {
    const res = await request(app).get("/nope");

    expect(res.status).toBe(404);
    expect(res.body.error).toBe("Not found: GET /nope");
  });
});

describe("error handling", () => {
  function appWithRoute(path: string, handler: express.RequestHandler) {
    const app = express();
    app.get(path, handler);
    app.use(errorHandler);
    return app;
  }

  it("maps HttpError to its status and message", async () => {
    const failing = appWithRoute("/teapot", () => {
      throw new HttpError(418, "I'm a teapot");
    });

    const res = await request(failing).get("/teapot");

    expect(res.status).toBe(418);
    expect(res.body.error).toBe("I'm a teapot");
  });

  it("maps unexpected errors to a generic 500", async () => {
    const failing = appWithRoute("/boom", async () => {
      throw new Error("secret internal detail");
    });

    const res = await request(failing).get("/boom");

    expect(res.status).toBe(500);
    expect(res.body.error).toBe("Internal server error");
  });
});
