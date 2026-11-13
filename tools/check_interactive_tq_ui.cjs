/* Run against the loopback demo with an existing Playwright installation and Chrome. */
"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");

async function main() {
  const port = Number(process.env.ORBIOS_DEMO_PORT || 8765);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid local port.");
  const base = "http://127.0.0.1:" + port;
  const output = process.argv[2] ? path.resolve(process.argv[2]) : null;
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_BINARY || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true,
    args: ["--no-proxy-server", "--disable-background-networking", "--disable-component-update",
      "--disable-sync", "--disable-domain-reliability", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"]
  });
  const checks = [];
  const context = await browser.newContext({viewport: {width: 1350, height: 1000}});
  await context.route("**/*", route => {
    const url = new URL(route.request().url());
    return url.origin === base ? route.continue() : route.abort();
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.setDefaultTimeout(12000);
  try {
    await page.goto(base);
    await page.getByText("SIMULATION — NO LIVE ORBIOS DATA", {exact: true}).waitFor();
    await page.getByRole("heading", {name: "Ask Telemetry", exact: true}).waitFor();
    const crossOrigin = await context.request.post(base + "/api/request", {
      headers: {Origin: "https://example.invalid"}, data: {action: "inspect"}
    });
    assert.equal(crossOrigin.status(), 403);
    checks.push("Loopback API rejects browser requests from a different origin");
    await page.getByRole("button", {name: "Temperature now", exact: true}).click();
    await page.locator("#readings .reading > p").filter({hasText: /Latest SoC processor temperature: 58 °C/}).waitFor();
    assert.equal(await page.locator("#query-lines").textContent(), "Q 1 P001 1m now");
    checks.push("Question to validated query, synthetic value and provenance");
    await page.locator("#question").fill("What is the input voltage right now?");
    await page.locator("#mode").selectOption("minilm");
    await page.locator("#draft-notice").waitFor();
    assert.equal(await page.locator("#submitted-question").textContent(), "What is the SoC temperature right now?");
    assert.match(await page.locator("#submitted-mode").textContent(), /Hybrid/);
    await page.getByText("Explain this answer", {exact: true}).click();
    const explanation = page.locator("#decisions");
    assert.equal(await explanation.locator("pre").textContent(), "Q 1 P001 1m now");
    assert.match(await explanation.textContent(), /Symbolic match.*Synthetic fixture training-orbit-v1.*complete/s);
    await page.locator("#mode").selectOption("hybrid");
    checks.push("Submitted question, mode, diagnostics and provenance stay linked after editing");
    await page.getByRole("button", {name: "Partial history", exact: true}).click();
    await page.locator("#readings .reading > p").filter({hasText: /Unobserved periods prevent a claim/}).waitFor();
    assert.match(await page.locator("#readings").textContent(), /partial/);
    await page.getByText("Explain this answer", {exact: true}).click();
    assert.match(await explanation.textContent(), /Longest gap1200 s/);
    checks.push("Zero events retains partial coverage");
    await page.getByRole("button", {name: "Ambiguous", exact: true}).click();
    await page.getByText("clarification required", {exact: true}).waitFor();
    assert.equal(await page.locator("#query-section").isVisible(), false);
    await page.getByText("Explain this answer", {exact: true}).click();
    assert.match(await explanation.textContent(), /No query was issued.*Not available/s);
    checks.push("Ambiguity asks for clarification without a query");
    await page.locator("#question").fill("What is the chip warmth right now?");
    await page.getByRole("button", {name: "Compare modes", exact: true}).click();
    await page.getByRole("heading", {name: "Mode comparison"}).waitFor();
    assert.equal(await page.locator(".compare-grid article").count(), 3);
    assert.match(await page.locator(".compare-grid").textContent(), /Q 1 P001 1m now/);
    await page.getByRole("button", {name: "Explain MiniLM", exact: true}).click();
    assert.match(await page.locator("#submitted-mode").textContent(), /MiniLM/);
    assert.match(await explanation.textContent(), /cosine similarity \d/);
    checks.push("All three modes run through the local bridge");
    if (output) {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({path: path.join(output, "demo-desktop.png"), animations: "disabled"});
    }
    await page.locator("#readings").getByRole("link", {name: "P001", exact: true}).click();
    await page.locator("#metric-detail").getByText("Synthetic history · last hour").waitFor();
    assert.match(await page.locator("#metric-detail").textContent(), /warning 65 °C/);
    assert.equal(await page.locator("#catalogue .capability").count(), 63);
    await page.locator("#catalogue-group").selectOption("Power rails");
    await page.locator("#filter").fill("vdd_core voltage");
    assert.equal(await page.locator("#catalogue .capability").count(), 1);
    await page.locator("#catalogue").getByRole("link", {name: "P170", exact: true}).click();
    await page.locator("#metric-detail").getByText(/P022 measures the SoC’s own voltage/).waitFor();
    assert.match(await page.locator("#metric-detail").textContent(), /Documented sample interval10 s/);
    await page.locator("#catalogue-group").selectOption("");
    await page.locator("#filter").fill("");
    await page.locator("#catalogue-type").selectOption("event");
    await page.locator("#catalogue-availability").selectOption("unavailable");
    assert.equal(await page.locator("#catalogue .capability").count(), 1);
    await page.locator("#catalogue").getByRole("link", {name: "A.wildfire.alert", exact: true}).click();
    await page.locator("#metric-detail").getByText(/no queryable synthetic history/).waitFor();
    assert.equal(await page.locator("#metric-detail pre").count(), 0);
    checks.push("Runtime catalogue grouping, combined filters, sensor metadata and unavailable declarations");
    await page.locator("#catalogue-type").selectOption("");
    await page.locator("#catalogue-availability").selectOption("");
    await page.locator("#catalogue").getByRole("link", {name: "P001", exact: true}).click();
    await page.locator("#metric-detail").getByText("Synthetic history · last hour").waitFor();
    if (output) await page.screenshot({path: path.join(output, "catalogue-desktop.png"), animations: "disabled"});
    await page.getByRole("link", {name: "How It Works", exact: true}).click();
    assert.equal(await page.locator("#pipeline-diagram button").count(), 7);
    await page.locator("#pipeline-diagram").getByRole("button", {name: "Validate and compile", exact: true}).click();
    assert.match(await page.locator("#pipeline-stages details[open]").textContent(), /Q 1 P001 24h stats/);
    if (output) {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({path: path.join(output, "pipeline-desktop.png"), animations: "disabled"});
    }
    await page.getByRole("button", {name: "Run in Ask Telemetry", exact: true}).click();
    await page.locator("#query-lines").getByText("Q 1 P001 24h stats", {exact: true}).waitFor();
    checks.push("Pipeline stages expand and the worked example runs through the actual planner");
    await page.getByRole("link", {name: "Telemetry Catalogue", exact: true}).click();
    await page.getByText("Edit fictional applications", {exact: true}).click();
    await page.locator("#manifest [name=id]").fill("test_halo_probe");
    await page.locator("#manifest [name=label]").fill("Test halo probe");
    await page.locator("[name=metric_id]").fill("glow_marker");
    await page.locator("[name=metric_label]").fill("Glow marker");
    await page.locator("[name=kind]").selectOption("event");
    await page.getByRole("button", {name: "Save manifest", exact: true}).click();
    await page.getByText(/Semantic index rebuilt in/).waitFor();
    await page.locator("#filter").fill("test_halo_probe");
    await page.getByRole("link", {name: "A.test_halo_probe.glow_marker", exact: true}).click();
    await page.locator("#metric-detail").getByText("Synthetic history · last hour").waitFor();
    await page.getByRole("link", {name: "Ask Telemetry", exact: true}).click();
    await page.locator("#mode").selectOption("symbolic");
    await page.locator("#question").fill("Count glow marker events from test_halo_probe in the last hour.");
    await page.getByRole("button", {name: "Ask telemetry", exact: true}).click();
    await page.locator("#query-lines").getByText("Q 1 A.test_halo_probe.glow_marker 1h count", {exact: true}).waitFor();
    checks.push("New custom metric becomes queryable and rebuilds the index");
    await page.locator("#readings").getByRole("link", {name: "A.test_halo_probe.glow_marker", exact: true}).click();
    await page.locator("[name=registered]").uncheck();
    await page.locator("#manifest [name=version]").fill("1.1-sim");
    await page.getByRole("button", {name: "Save manifest", exact: true}).click();
    await page.locator("#catalogue .capability").getByText(/Unavailable/).waitFor();
    await page.locator("#metric-detail").getByText(/Test halo probe · 1.1-sim/).waitFor();
    await page.getByRole("link", {name: "Ask Telemetry", exact: true}).click();
    await page.getByRole("button", {name: "Ask telemetry", exact: true}).click();
    await page.getByText("unavailable", {exact: true}).waitFor();
    assert.equal(await page.locator("#query-section").isVisible(), false);
    checks.push("Manifest edit removes a metric from available readings");
    await page.getByRole("link", {name: "Telemetry Catalogue", exact: true}).click();
    await page.getByRole("link", {name: "A.test_halo_probe.glow_marker", exact: true}).click();
    await page.getByRole("button", {name: "Remove app", exact: true}).click();
    await page.getByText("No matching capabilities.", {exact: true}).waitFor();
    await page.getByText("This capability is absent from the current catalogue.", {exact: true}).waitFor();
    checks.push("Remove app clears its catalogue entries");
    await page.setViewportSize({width: 390, height: 844});
    await page.locator("#filter").fill("");
    for (const view of ["How It Works", "Telemetry Catalogue", "Ask Telemetry"]) {
      await page.getByRole("link", {name: view, exact: true}).click();
      await page.getByRole("heading", {name: view, exact: true}).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
      await page.getByText("SIMULATION — NO LIVE ORBIOS DATA", {exact: true}).waitFor();
    }
    await page.getByRole("button", {name: "Stale voltage", exact: true}).click();
    await page.locator("#readings .reading > p").filter({hasText: /Latest Pi EXT5V input supply voltage: 5.02 V/}).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    assert.deepEqual(errors, []);
    checks.push("Mobile width fits; stale value and coverage are distinct");
    await page.evaluate(() => window.scrollTo(0, 0));
    if (output) await page.screenshot({path: path.join(output, "demo-mobile.png"), animations: "disabled"});
    await page.getByRole("button", {name: "Add and query a fictional app", exact: true}).click();
    await page.locator("#query-lines").getByText(/A\.demo_survey\.marker_event 1h count/).waitFor();
    await page.locator("#example-app-status").getByRole("link").click();
    await page.locator("#metric-detail").getByText(/Fictional manifest/, {exact: true}).waitFor();
    checks.push("Guided example adds, queries and opens a fictional capability");
    console.log(JSON.stringify({checks, pageErrors: errors}, null, 2));
  } finally {
    // Restore the initial session if a browser assertion interrupted the edit.
    const state = await context.request.get(base + "/api/state");
    if (state.ok()) {
      for (const app of (await state.json()).data.apps) {
        if (["test_halo_probe", "demo_survey"].includes(app.id)) {
          await context.request.post(base + "/api/request", {data: {action: "remove", id: app.id}});
        }
      }
    }
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
