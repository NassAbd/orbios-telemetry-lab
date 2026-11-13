"use strict";

const $ = (selector) => document.querySelector(selector);
const element = (tag, content, className) => {
  const node = document.createElement(tag);
  if (content !== undefined) node.textContent = content;
  if (className) node.className = className;
  return node;
};
const names = {symbolic: "Symbolic", minilm: "MiniLM", hybrid: "Hybrid"};
const callCount = (count) => count + (count === 1 ? " semantic call" : " semantic calls");
const examples = [
  ["Temperature now", "What is the SoC temperature right now?", "Latest numeric sample"],
  ["Temperature statistics", workedQuestion, "Min, max and mean over 24 hours"],
  ["Heat exposure", "How long was SoC temperature above 65 °C in the last minute?", "Time above a threshold"],
  ["Application state", "Is ship detection running now?", "Job state and transitions"],
  ["Detection count", "How many events did A.ship_detection.detected record in the last hour?", "Observed detections in one hour"],
  ["Partial history", "How many false alerts did oil leak record in the last hour?", "Zero observed events with a gap"],
  ["Ambiguous", "What is the voltage?", "Clarification before compilation"],
  ["Stale voltage", "What is the input voltage right now?", "Sample age and window coverage"],
  ["Two metrics", "What were the average SoC temperature and board power in the last hour?", "Two validated query lines"],
  ["Latest event", "When was the most recent ship detection event?", "Last observed detection"],
];
let state = null;
let busy = false;
let submitted = null;

async function api(action, data = {}) {
  const response = action === "inspect" ? await fetch("/api/state") : await fetch("/api/request", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({action, ...data})
  });
  const result = await response.json();
  if (!result.ok) throw new Error(result.error || "Request failed.");
  return result.data;
}

async function refresh(preferred) {
  state = await api("inspect");
  $("#clock").textContent = state.clock.replace("T", " ").replace("+00:00", " UTC");
  $("#scenario").textContent = "· " + state.scenario + " · seed " + state.seed + " · catalogue r" + state.revision;
  const picker = $("#app-picker");
  const id = preferred ?? picker.value;
  picker.replaceChildren(new Option("New application", ""));
  for (const app of state.apps) picker.append(new Option(app.label + " (" + app.id + ")", app.id));
  picker.value = state.apps.some(app => app.id === id) ? id : "";
  const groups = $("#catalogue-group");
  const previousGroup = groups.value;
  groups.replaceChildren(new Option("All groups", ""), ...state.groups.map(group => new Option(group, group)));
  groups.value = state.groups.includes(previousGroup) ? previousGroup : "";
  showCatalogue();
  if (activeView === "telemetry-catalogue") void showMetric();
}

function updateDraftNotice() {
  $("#draft-notice").hidden = !submitted || $("#question").value.trim() === submitted.question;
}

function showSubmission(question, mode, revision) {
  submitted = {question, mode};
  $("#submitted-question").textContent = question;
  $("#submitted-mode").textContent = names[mode] + (revision ? " · catalogue r" + revision : "");
  $("#submission").hidden = false;
  updateDraftNotice();
}

function readingCard(r, link = true) {
  const card = element("article", undefined, "reading");
  const header = element("div", undefined, "panel-head");
  const badges = element("div", undefined, "buttons");
  badges.append(element("span", r.completeness, "badge " + r.completeness));
  if (r.packet.freshness) badges.append(element("span", r.packet.freshness, "badge " + r.packet.freshness));
  header.append(link ? metricLink(r.packet.param) : element("code", r.packet.param), badges);
  const packet = element("details");
  packet.append(element("summary", "Raw synthetic answer packet"), element("pre", r.packet.raw), element("p", r.text));
  const f = r.packet.fields;
  const headline = r.text.split(/ (?=Freshness:|Longest gap in seconds:|Material gap count:|Resolution in seconds:|Catalog version:|Source: tq packet)/)[0];
  card.append(header, element("p", headline));
  if (r.packet.freshness && r.completeness !== "complete") {
    card.append(element("p", "The requested window has " + (r.completeness === "nodata" ? "no observations." : "partial coverage.")));
  }
  card.append(element("small", r.source + (Number(f.gap) ? " · Longest gap " + f.gap + " s" : "")), packet);
  return card;
}

function explain(result) {
  const explanation = $("#decisions");
  const t = result.translation;
  const metrics = result.metrics || {};
  const selected = element("ul");
  for (const id of result.selected) {
    const row = element("li");
    row.append(metricLink(id), " · " + (metrics[id]?.description ?? unavailable));
    selected.append(row);
  }
  const scopes = [...new Set(result.selected.map(id => {
    const p = metrics[id];
    return p?.application_label || p?.application_id || p?.group;
  }).filter(Boolean))];
  explanation.replaceChildren(fieldList([
    ["Submitted question", result.question],
    ["Mode", names[result.mode]],
    ["Application or scope linked by selected metrics", scopes.join(", ") || null],
    ["Selected metrics", result.selected.length ? selected : "None"],
    ["Semantic calls", result.semantic_calls],
    ["Session catalogue revision", "r" + result.catalogue_revision],
    ["Clarification or abstention reason", t.explanation || (t.status === "answer" ? "Not applicable" : null)],
    ["Assumptions", t.assumptions.join(" ") || "None recorded"],
  ]), element("h3", "Matching evidence"));
  if (!result.decisions.length) explanation.append(element("p", unavailable));
  const methods = {symbolic: "Symbolic match", semantic: "MiniLM similarity search", guard: "Eligibility or ambiguity check"};
  for (const d of result.decisions) {
    const item = element("div", undefined, "diag");
    item.append(element("strong", d.clause), element("p", methods[d.method] + " · " + d.reason));
    if (d.candidates.length) {
      const list = element("ul");
      for (const c of d.candidates) {
        const row = element("li");
        row.append(metricLink(c.id), " · " + (metrics[c.id]?.description ?? unavailable));
        if (c.score !== null) row.append(" · cosine similarity " + c.score.toFixed(4));
        list.append(row);
      }
      item.append(list);
    } else item.append(element("p", "Candidate metrics · " + unavailable));
    explanation.append(item);
  }
  if (result.decisions.some(d => d.candidates.some(c => c.score !== null))) {
    explanation.append(element("p", "Cosine similarity measures how close the descriptions are to the question. It is not a probability.", "muted"));
  }
  explanation.append(element("h3", "Validated tq queries"));
  if (result.lines.length) explanation.append(element("pre", result.lines.join("\n")));
  else explanation.append(element("p", "No query was issued."));
  for (const [index, q] of t.queries.entries()) {
    const unit = metrics[q.param]?.unit;
    const threshold = ["above", "below"].includes(q.op) && q.arg !== null;
    explanation.append(element("h3", "Query " + q.id + " · " + q.param), fieldList([
      ["Operation", q.op], ["Window", q.window], ["Requested end", q.end || "Latest synthetic time"],
      ...(q.arg !== null ? [[threshold ? "Threshold" : "Argument", q.arg + (threshold && unit ? " " + unit : "")]] : []),
    ]));
    const r = result.readings[index];
    if (!r) {
      explanation.append(element("p", "Synthetic provenance · " + unavailable));
      continue;
    }
    const f = r.packet.fields;
    explanation.append(fieldList([
      ["Synthetic history source", r.source], ["Coverage", r.completeness],
      ["Freshness", r.packet.freshness], ["Actual window end (UTC)", f.end],
      ["Actual window", f.w], ["Resolution", f.res && f.res !== "0" ? f.res + " s" : null],
      ["Longest gap", f.gap === undefined ? null : f.gap + " s"], ["Gap count", f.gaps],
      ["Unit", unit], ["Packet catalogue version", f.cat], ["Limits version", f.lim],
      ...(f.ver ? [["Application version", f.ver]] : []),
      ...(f.age !== undefined ? [["Sample age at window end", f.age + " s"]] : []),
      ...[["from", "History starts"], ["first", "First observed match"], ["last", "Last observed match"], ["at", "Event time"]]
        .filter(([key]) => f[key] !== undefined).map(([key, label]) => [label, f[key] + " s relative to window end"]),
    ]));
  }
  if (!result.readings.length) explanation.append(element("p", "Synthetic provenance and coverage · " + unavailable));
  $("#diagnostics").hidden = false;
}

function showResult(result) {
  const t = result.translation;
  showSubmission(result.question, result.mode, result.catalogue_revision);
  const badge = $("#result-badge");
  badge.className = "badge " + t.status;
  badge.textContent = t.status.replaceAll("_", " ");
  $("#message").textContent = t.explanation || names[result.mode] + " · " + result.elapsed_ms.toFixed(2) + " ms · " + callCount(result.semantic_calls);
  $("#readings").replaceChildren(...result.readings.map(r => readingCard(r)));
  $("#query-section").hidden = !result.lines.length;
  $("#query-lines").textContent = result.lines.join("\n");
  $("#slots").replaceChildren(...t.queries.map(q => element("li",
    [q.param, q.op + (q.arg ? " " + q.arg : ""), q.window, q.end ? "ending " + q.end : ""].filter(Boolean).join(" · "))),
    ...t.assumptions.map(a => element("li", a)));
  explain(result);
}

function setBusy(value) {
  busy = value;
  $("#submit").disabled = value;
  $("#compare").disabled = value;
  $("#mode").disabled = value;
  $("#app-picker").disabled = value;
  $("#add-example-app").disabled = value;
  $("#run-worked-example").disabled = value;
  $("#manifest").querySelectorAll("button").forEach(button => { button.disabled = value; });
  $("#ask").setAttribute("aria-busy", String(value));
}

async function ask(compare = false) {
  if (busy || !$("#ask").reportValidity()) return;
  const question = $("#question").value.trim();
  if (!question) return;
  const mode = $("#mode").value;
  setBusy(true);
  showSubmission(question, mode);
  $("#message").textContent = compare ? "Comparing three modes…" : "Reading the synthetic archive…";
  $("#result-badge").textContent = "Working";
  $("#result-badge").className = "badge";
  $("#readings").replaceChildren();
  $("#query-section").hidden = true;
  $("#diagnostics").hidden = true;
  $("#diagnostics").open = false;
  $("#comparison").replaceChildren();
  try {
    if (compare) {
      const results = [];
      for (const name of Object.keys(names)) results.push(await api("ask", {question, mode: name}));
      showResult(results.find(r => r.mode === mode));
      const grid = element("div", undefined, "compare-grid");
      for (const r of results) {
        const card = element("article");
        card.append(element("strong", names[r.mode]), element("p", r.translation.status.replaceAll("_", " ")),
          element("p", r.elapsed_ms.toFixed(2) + " ms · " + callCount(r.semantic_calls)));
        if (r.lines.length) card.append(element("pre", r.lines.join("\n")));
        else card.append(element("p", r.translation.explanation));
        const inspect = element("button", "Explain " + names[r.mode], "secondary small");
        inspect.type = "button";
        inspect.addEventListener("click", () => {
          showResult(r);
          $("#diagnostics").open = true;
          $("#diagnostics").querySelector("summary").focus();
        });
        card.append(inspect);
        grid.append(card);
      }
      $("#comparison").append(element("h3", "Mode comparison"), grid);
    } else showResult(await api("ask", {question, mode}));
  } catch (err) {
    $("#message").textContent = err.message;
    $("#result-badge").textContent = "Error";
  } finally {
    setBusy(false);
  }
}

function useExample(question, mode) {
  if (busy) return;
  $("#question").value = question;
  if (mode) $("#mode").value = mode;
  navigate("ask-telemetry");
  renderRoute();
  void ask();
}

function metricRow(spec = {}) {
  if ($("#metrics").children.length >= 12) return;
  const row = $("#metric-template").content.firstElementChild.cloneNode(true);
  const set = (name, value) => { row.querySelector("[name=" + name + "]").value = value; };
  set("metric_id", spec.id || "");
  set("metric_label", spec.label || "");
  set("kind", spec.kind || "number");
  set("unit", spec.unit || "");
  set("profile", spec.profile || "complete");
  set("aliases", (spec.aliases || []).join(", "));
  row.querySelector("[name=registered]").checked = spec.registered !== false;
  row.querySelector(".remove-metric").addEventListener("click", () => row.remove());
  $("#metrics").append(row);
}

function loadEditor() {
  const app = state?.apps.find(a => a.id === $("#app-picker").value);
  const form = $("#manifest");
  for (const [key, fallback] of Object.entries({id: "", label: "", version: "1.0-sim", budget_s: 180})) {
    form.elements[key].value = app?.[key] ?? fallback;
  }
  form.elements.id.readOnly = Boolean(app);
  $("#remove-app").hidden = !app;
  $("#metrics").replaceChildren();
  if (app) app.metrics.forEach(metricRow);
  else metricRow();
}

function manifestPayload() {
  const form = $("#manifest");
  return {
    id: form.elements.id.value, label: form.elements.label.value, version: form.elements.version.value,
    budget_s: Number(form.elements.budget_s.value),
    metrics: Array.from($("#metrics").children).map(row => {
      const value = (name) => row.querySelector("[name=" + name + "]").value;
      return {id: value("metric_id"), label: value("metric_label"), kind: value("kind"),
        unit: value("unit") || null, profile: value("profile"),
        aliases: value("aliases").split(",").map(x => x.trim()).filter(Boolean),
        registered: row.querySelector("[name=registered]").checked};
    })
  };
}

async function edit(action) {
  if (busy) return;
  setBusy(true);
  $("#editor-status").textContent = "Updating fictional catalogue…";
  try {
    const manifest = manifestPayload();
    const update = await api(action, action === "save" ? {manifest} : {id: manifest.id});
    await refresh(action === "save" ? manifest.id : "");
    loadEditor();
    $("#editor-status").textContent = "Catalogue updated to r" + update.revision + ". " +
      (update.index_ms === null ? "MiniLM will index this revision on first use." :
        "Semantic index rebuilt in " + update.index_ms.toFixed(1) + " ms.");
    $("#message").textContent = "Catalogue updated. Ask again to use the latest revision.";
    submitted = null;
    $("#submission").hidden = true;
    $("#readings").replaceChildren();
    $("#query-section").hidden = true;
    $("#diagnostics").hidden = true;
    $("#comparison").replaceChildren();
    $("#result-badge").textContent = "Ready";
    $("#result-badge").className = "badge";
  } catch (err) {
    $("#editor-status").textContent = err.message;
  } finally {
    setBusy(false);
  }
}

async function addExampleApplication() {
  if (busy || !state) return;
  setBusy(true);
  let id = "demo_survey";
  let suffix = 2;
  while (state.apps.some(app => app.id === id || app.label.toLowerCase() === id.replaceAll("_", " "))) {
    id = "demo_survey_" + suffix++;
  }
  let question;
  try {
    await api("save", {manifest: {
      id, label: id.replaceAll("_", " "), version: "1.0-sim", budget_s: 180,
      metrics: [{id: "marker_event", label: "Survey marker events", kind: "event"}],
    }});
    await refresh(id);
    loadEditor();
    question = "How many events did A." + id + ".marker_event record in the last hour?";
    const status = $("#example-app-status");
    status.replaceChildren("Added ", metricLink("A." + id + ".marker_event"), " to this session.");
  } catch (err) {
    $("#example-app-status").textContent = err.message;
  } finally {
    setBusy(false);
  }
  if (question) useExample(question);
}

for (const [label, question, context] of examples) {
  const button = element("button", label);
  button.type = "button";
  button.append(element("small", context));
  button.setAttribute("aria-label", label);
  button.addEventListener("click", () => useExample(question));
  $("#examples").append(button);
}
$("#ask").addEventListener("submit", event => {event.preventDefault(); void ask();});
$("#compare").addEventListener("click", () => void ask(true));
$("#question").addEventListener("input", updateDraftNotice);
for (const filter of ["#filter", "#catalogue-group", "#catalogue-type", "#catalogue-availability"]) {
  $(filter).addEventListener("input", () => {if (state) showCatalogue();});
}
$("#add-example-app").addEventListener("click", () => void addExampleApplication());
$("#app-picker").addEventListener("change", loadEditor);
$("#add-metric").addEventListener("click", () => metricRow());
$("#manifest").addEventListener("submit", event => {event.preventDefault(); void edit("save");});
$("#remove-app").addEventListener("click", () => void edit("remove"));
window.addEventListener("hashchange", () => renderRoute(true));
setupPipeline();
renderRoute();
refresh().then(loadEditor).catch(err => {
  $("#message").textContent = err.message;
  $("#metric-detail").textContent = err.message;
});
