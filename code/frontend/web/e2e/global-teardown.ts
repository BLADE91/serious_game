import { rm } from "node:fs/promises";

export default async function globalTeardown() {
  const storageState = process.env.FULL_E2E_STORAGE_STATE;
  if (storageState) await rm(storageState, { force: true });
}
