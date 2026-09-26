import { Router } from "express";
import { HttpError } from "../errors/http-error.js";
import { requireAuth } from "../middleware/require-auth.js";
import { getProductPurchase, PURCHASE_STATE_PURCHASED } from "../services/play-billing.js";
import { grantPurchase } from "../services/credits.js";

export const billingRouter: Router = Router();

// The app calls this with the purchase token from Play Billing. Credits are
// granted server-side; the app must consume the purchase only after a 2xx.
billingRouter.post("/verify", requireAuth, async (req, res) => {
  const { productId, purchaseToken } = (req.body ?? {}) as {
    productId?: unknown;
    purchaseToken?: unknown;
  };
  if (typeof productId !== "string" || productId.length === 0) {
    throw new HttpError(400, "productId is required");
  }
  if (typeof purchaseToken !== "string" || purchaseToken.length === 0) {
    throw new HttpError(400, "purchaseToken is required");
  }

  let purchase;
  try {
    purchase = await getProductPurchase(productId, purchaseToken);
  } catch {
    throw new HttpError(400, "Purchase could not be verified with Google Play");
  }
  if (purchase.purchaseState !== PURCHASE_STATE_PURCHASED) {
    throw new HttpError(400, "Purchase is not in a purchased state");
  }

  const result = await grantPurchase(req.user!.id, productId, purchaseToken, purchase.orderId);
  res.status(result.alreadyGranted ? 200 : 201).json(result);
});
