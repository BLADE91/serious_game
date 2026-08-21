import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const shell = await readFile(new URL("../app/GameShell.tsx", import.meta.url), "utf8");

test("announces the Baixiaosheng redemption purpose before a game starts", () => {
  assert.match(shell, /通晓币不用于人物会谈或本局行动消耗/);
  assert.match(shell, /后续“百晓生”网站兑换/);
  assert.match(shell, /具体规则以百晓生网站公告为准/);
  assert.doesNotMatch(shell, /通晓币用于需要模型参与的人物会谈/);
});
