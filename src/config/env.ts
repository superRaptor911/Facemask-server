import "dotenv/config";

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

export const env = {
  nodeEnv: process.env.NODE_ENV ?? "development",
  port: Number(process.env.PORT ?? 3000),
  databaseUrl: required("DATABASE_URL"),
  // Web-application OAuth client ID — the audience Google ID tokens must carry.
  googleClientId: required("GOOGLE_CLIENT_ID"),
  // Android package name; only needed for Play Billing verification + voided-purchases job.
  playPackageName: process.env.PLAY_PACKAGE_NAME ?? "",
} as const;
