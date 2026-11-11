async (page) => {
  const result = {};

  // 1) Language cycle: currently zh; click -> ja -> en -> zh
  const langBtn = page.locator(".lang-btn");
  result.startLang = await langBtn.textContent();
  await langBtn.click();
  await page.waitForTimeout(300);
  result.after1 = await langBtn.textContent();
  result.sampleJa = await page.locator(".tabs button").first().textContent().catch(() => null);
  await langBtn.click();
  await page.waitForTimeout(300);
  result.after2 = await langBtn.textContent();
  result.sampleEn = await page.locator(".tabs button").first().textContent().catch(() => null);
  await langBtn.click();
  await page.waitForTimeout(300);
  result.after3 = await langBtn.textContent();
  result.backToZh = (await page.locator("button", { hasText: "生成" }).count()) > 0;

  // 2) Scroll the roll with the wheel, then click INSIDE the roll (visible window):
  //    the view must NOT jump (only outside seeks follow).
  const roll = page.locator(".roll canvas");
  const box = await roll.boundingBox();
  await page.mouse.wheel(0, 0); // noop
  const cx = box.x + 30;
  const cy = box.y + box.height / 2;
  // pan left with wheel deltaX
  await page.mouse.move(cx, cy);
  await page.evaluate(() => { /* wheel with deltaX needs CDP or event; use keyboard-free approach */ });
  // Click near left edge of the current (3:22..) window -> time should be ~3:23 and stay in view
  await page.mouse.click(box.x + 40, cy);
  await page.waitForTimeout(400);
  result.timeAfterInsideClick = await page.locator(".player-seek .mono").first().textContent();

  return JSON.stringify(result, null, 2);
}
