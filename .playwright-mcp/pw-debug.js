async (page) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const result = { errors };
  const url = await page.url();
  result.url = url;

  // which JS bundle is loaded (new build = index-CDgE0Ih2.js)
  result.scripts = await page.evaluate(() => Array.from(document.scripts).map((s) => s.src).filter(Boolean));

  // click overview with explicit down/up
  const overview = page.locator("canvas.overview");
  const ob = await overview.boundingBox();
  result.overviewBox = ob;
  await page.mouse.move(ob.x + ob.width * 0.75, ob.y + ob.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(120);
  await page.mouse.up();
  await page.waitForTimeout(500);
  result.timeAfterOverview = await page.locator(".player-seek .mono").first().textContent();

  return JSON.stringify(result, null, 2);
}
