import { Router } from "express";
import { authRouter } from "./auth.js";
import { meRouter } from "./me.js";
import { creditsRouter } from "./credits.js";
import { billingRouter } from "./billing.js";
import { accountRouter } from "./account.js";

export const v1Router: Router = Router();

v1Router.use("/auth", authRouter);
v1Router.use("/me", meRouter);
v1Router.use("/credits", creditsRouter);
v1Router.use("/billing", billingRouter);
v1Router.use("/account", accountRouter);
