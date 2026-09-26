import { androidpublisher, auth } from "@googleapis/androidpublisher";
import { env } from "../config/env.js";

// Credentials come from GOOGLE_APPLICATION_CREDENTIALS (Play service-account
// JSON key). GoogleAuth resolves them lazily, on the first API call.
const publisher = androidpublisher({
  version: "v3",
  auth: new auth.GoogleAuth({
    scopes: ["https://www.googleapis.com/auth/androidpublisher"],
  }),
});

export const PURCHASE_STATE_PURCHASED = 0;

function packageName(): string {
  if (!env.playPackageName) {
    throw new Error("PLAY_PACKAGE_NAME is not set — required for Play Billing calls");
  }
  return env.playPackageName;
}

export interface ProductPurchase {
  purchaseState: number;
  orderId?: string;
}

export async function getProductPurchase(
  productId: string,
  purchaseToken: string,
): Promise<ProductPurchase> {
  const res = await publisher.purchases.products.get({
    packageName: packageName(),
    productId,
    token: purchaseToken,
  });
  return {
    purchaseState: res.data.purchaseState ?? -1,
    orderId: res.data.orderId ?? undefined,
  };
}

export interface VoidedPurchasesPage {
  voidedPurchases: { purchaseToken: string }[];
  nextPageToken?: string;
}

export async function listVoidedPurchases(pageToken?: string): Promise<VoidedPurchasesPage> {
  const res = await publisher.purchases.voidedpurchases.list({
    packageName: packageName(),
    ...(pageToken ? { token: pageToken } : {}),
  });
  return {
    voidedPurchases: (res.data.voidedPurchases ?? []).flatMap((vp) =>
      typeof vp.purchaseToken === "string" ? [{ purchaseToken: vp.purchaseToken }] : [],
    ),
    nextPageToken: res.data.tokenPagination?.nextPageToken ?? undefined,
  };
}
