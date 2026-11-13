"use strict";

const unavailable = "Not available";
const viewIds = ["ask-telemetry", "telemetry-catalogue", "how-it-works"];
let activeView = "ask-telemetry";
let activeMetric = "";
let detailRequest = 0;

function fieldList(entries) {
  const list = element("dl", undefined, "field-list");
  for (const [name, value] of entries) {
    const cell = element("dd");
    if (value instanceof Node) cell.append(value);
    else cell.textContent = value ?? unavailable;
    list.append(element("dt", name), cell);
  }
  return list;
}

function metricLink(id, label = id) {
  const link = element("a", label, "metric-link");
  link.href = "#telemetry-catalogue/" + encodeURIComponent(id);
  return link;
}

function navigate(view) {
  if (location.hash === "#" + view) renderRoute();
  else location.hash = view;
}

function renderRoute(focus = false) {
  const [requested, metric = ""] = location.hash.slice(1).split("/");
  activeView = viewIds.includes(requested) ? requested : "ask-telemetry";
  try {
    activeMetric = activeView === "telemetry-catalogue" ? decodeURIComponent(metric) : "";
  } catch {
    activeMetric = "";
  }
  for (const view of document.querySelectorAll("[data-view]")) {
    view.hidden = view.dataset.view !== activeView;
  }
  for (const link of document.querySelectorAll("[data-nav]")) {
    if (link.dataset.nav === activeView) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (focus) {
    window.scrollTo(0, 0);
    document.querySelector("[data-view]:not([hidden]) h1").focus({preventScroll: true});
  }
  if (activeView === "telemetry-catalogue" && state) {
    showCatalogue();
    void showMetric(focus && Boolean(activeMetric));
  }
}

function showCatalogue() {
  const query = $("#filter").value.toLowerCase().trim().split(/\s+/);
  const group = $("#catalogue-group").value;
  const kind = $("#catalogue-type").value;
  const availability = $("#catalogue-availability").value;
  const list = $("#catalogue");
  const previousScroll = list.scrollTop;
  list.replaceChildren();
  const rows = state.catalogue.filter(p => {
    const text = [p.id, p.description, p.unit, p.application_id, ...p.aliases].filter(Boolean).join(" ").toLowerCase();
    return query.every(word => text.includes(word)) &&
      (!group || p.group === group) &&
      (!kind || p.kind === kind) &&
      (!availability || p.registered === (availability === "registered"));
  });
  $("#catalogue-count").textContent = rows.length + " / " + state.catalogue.length;
  for (const name of state.groups) {
    const matching = rows.filter(p => p.group === name);
    if (!matching.length) continue;
    const section = element("section", undefined, "capability-group");
    section.append(element("h3", name));
    for (const p of matching) {
      const row = element("article", undefined, "capability");
      const link = metricLink(p.id);
      if (p.id === activeMetric) link.setAttribute("aria-current", "true");
      const head = element("div", undefined, "panel-head");
      head.append(link, element("span", p.registered ? "Registered" : "Unavailable", "badge " + (p.registered ? "" : "unavailable")));
      row.append(head, element("p", p.description),
        element("small", [p.kind === "apps" ? "inventory" : p.kind, p.unit].filter(Boolean).join(" · ")));
      section.append(row);
    }
    list.append(section);
  }
  if (!rows.length) list.append(element("p", "No matching capabilities."));
  list.scrollTop = previousScroll;
}

async function showMetric(focus = false) {
  const request = ++detailRequest;
  const id = activeMetric;
  const title = $("#metric-title");
  const body = $("#metric-detail");
  title.textContent = id || "Metric details";
  body.replaceChildren();
  if (focus) {
    title.focus({preventScroll: true});
    if (matchMedia("(max-width: 850px)").matches) title.scrollIntoView({block: "start"});
  }
  if (!id) {
    body.append(element("p", "Select a capability to inspect its metadata and synthetic history."));
    return;
  }
  if (!state.catalogue.some(p => p.id === id)) {
    body.append(element("p", "This capability is absent from the current catalogue."));
    return;
  }
  body.append(element("p", "Reading metric details…"));
  try {
    const p = await api("metric", {id});
    if (request !== detailRequest || activeView !== "telemetry-catalogue") return;
    title.textContent = p.name;
    const identity = element("p", undefined, "metric-identity");
    identity.append(element("code", p.id), element("span", p.registered ? "Registered" : "Unavailable", "badge " + (p.registered ? "" : "unavailable")));
    const limits = Object.entries(p.limits).map(([name, value]) => name + " " + value + (p.unit ? " " + p.unit : ""));
    const app = p.application_id && [p.application_label || p.application_id, p.application_version || "version not available"].join(" · ");
    body.replaceChildren(identity);
    if (p.description !== p.name) body.append(element("p", p.description));
    body.append(fieldList([
      ["Group", p.group],
      ["Type", p.kind === "apps" ? "Application inventory" : p.kind],
      ["Unit", p.unit],
      [p.registered ? "Supported tq operations" : "Operations defined for this type", p.operations.join(", ")],
      ["Documented sample interval", p.documented_cadence_s === null ? null : p.documented_cadence_s + " s"],
      ...(p.every_s !== null && p.every_s !== p.documented_cadence_s ? [["Synthetic sample interval", p.every_s + " s"]] : []),
      ["Thresholds and limits", limits.length ? limits.join(", ") : null],
      ...(app ? [["Application", app]] : []),
      ["Origin", p.origin],
      ["Catalogue source", p.provenance],
      ["Session catalogue revision", "r" + p.revision],
    ]));
    if (p.notes.length) {
      const notes = element("ul");
      notes.append(...p.notes.map(note => element("li", note)));
      body.append(notes);
    }
    if (p.example) {
      const button = element("button", "Try this question", "secondary");
      button.type = "button";
      button.addEventListener("click", () => useExample(p.example.question));
      body.append(element("h3", "Example question"), element("p", p.example.question),
        element("pre", p.example.line), button);
    } else {
      body.append(element("p", "A validated query example becomes available when this capability is registered.", "muted"));
    }
    if (p.preview) {
      body.append(element("h3", "Synthetic history · last hour"), readingCard(p.preview, false));
    } else if (p.kind === "apps") {
      body.append(element("p", "The application inventory is a view of the current session.", "muted"));
    }
  } catch (err) {
    if (request === detailRequest && activeView === "telemetry-catalogue") {
      body.replaceChildren(element("p", err.message));
    }
  }
}
