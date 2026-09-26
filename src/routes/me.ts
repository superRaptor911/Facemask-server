import { Router } from "express";
import { toPublicUser } from "../lib/serializers.js";
import { requireAuth } from "../middleware/require-auth.js";

export const meRouter: Router = Router();

meRouter.get("/", requireAuth, (req, res) => {
  res.json(toPublicUser(req.user!));
});
