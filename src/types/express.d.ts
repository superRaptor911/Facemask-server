import type { Session, User } from "../generated/prisma/client.js";

declare global {
  namespace Express {
    interface Request {
      user?: User;
      session?: Session;
    }
  }
}

export {};
