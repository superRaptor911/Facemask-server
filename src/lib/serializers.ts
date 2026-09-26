import type { User } from "../generated/prisma/client.js";

export function toPublicUser(user: User) {
  return {
    id: user.id,
    email: user.email,
    name: user.name,
    pictureUrl: user.pictureUrl,
    creditBalance: user.creditBalance,
    createdAt: user.createdAt,
  };
}
