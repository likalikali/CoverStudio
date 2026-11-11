async (page) => {
  await page.goto("http://127.0.0.1:8189/p/VH%20-%20%E9%A3%9F%E4%BA%BA%E6%A2%A6", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  const roll = page.locator(".roll canvas");
  const box = await roll.boundingBox();
  const result = {};

  // 1) Paused: click near the roll's right edge (a time inside the visible window's last 10%)
  const x1 = box.x + box.width - 20;
  await page.mouse.click(x1, box.y + box.height / 2);
  await page.waitForTimeout(700);
  result.afterInWindowClick = await page.locator(".player-seek .mono").first().textContent();

  // 2) Paused: seek far away via the bottom overview bar (outside the visible window)
  const overview = page.locator("canvas.overview");
  const ob = await overview.boundingBox();
  await page.mouse.click(ob.x + ob.width * 0.75, ob.y + ob.height / 2);
  await page.waitForTimeout(700);
  result.afterOverviewSeek = await page.locator(".player-seek .mono").first().textContent();
  result.overviewDuration = await page.locator(".player-seek .mono").nth(1).textContent();

  return JSON.stringify(result);
}
