// Entry point for the scheduled refund-clawback job.
// Dev: npm run job:voided-purchases | Prod: node dist/jobs/voided-purchases.js
import { prisma } from "../lib/prisma.js";
import { processVoidedPurchases } from "../services/voided-purchases.js";

try {
  const count = await processVoidedPurchases();
  console.log(`Voided purchases clawed back: ${count}`);
} catch (err) {
  console.error(err);
  process.exitCode = 1;
} finally {
  await prisma.$disconnect();
}
