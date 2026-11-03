interface EnvConfig {
  API_BASE_URL: string;
}

export const envConfig: EnvConfig = {
  API_BASE_URL:
    process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api",
};

export function validateEnvConfig(): void {
  const missingVars = Object.entries(envConfig)
    .filter(([, value]) => value === "")
    .map(([key]) => key);

  if (missingVars.length > 0) {
    throw new Error(`Missing environment variables: ${missingVars.join(", ")}`);
  }
}

validateEnvConfig();
