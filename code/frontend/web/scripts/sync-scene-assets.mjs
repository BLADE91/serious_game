import { copyFile, mkdir, readdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { SCENE_ASSET_WHITELIST } from "./scene-asset-whitelist.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = path.resolve(projectRoot, "..", "photo", "全本场景图");
const outputRoot = path.resolve(projectRoot, "public", "scenes");

if (!outputRoot.startsWith(path.resolve(projectRoot, "public") + path.sep)) {
  throw new Error(`Refusing to sync outside public: ${outputRoot}`);
}

await mkdir(outputRoot, { recursive: true });
for (const entry of await readdir(outputRoot, { withFileTypes: true })) {
  if (!entry.isFile()) throw new Error(`public/scenes must remain flat: ${entry.name}`);
  await rm(path.join(outputRoot, entry.name), { force: true });
}

for (const item of SCENE_ASSET_WHITELIST) {
  const source = path.resolve(sourceRoot, item.source);
  const destination = path.resolve(outputRoot, item.destination);
  if (!source.startsWith(sourceRoot + path.sep) || !destination.startsWith(outputRoot + path.sep)) {
    throw new Error(`Unsafe scene asset path: ${item.source}`);
  }
  await copyFile(source, destination);
}

console.log(`Synced ${SCENE_ASSET_WHITELIST.length} approved WebP assets to public/scenes.`);
