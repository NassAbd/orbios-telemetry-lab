"use strict";

const workedQuestion = "What’s the maximum CPU temperature today?";
const stages = [
  {
    title: "English question",
    input: "The question and the mode selected in Ask Telemetry.",
    work: "The planner checks scope, splits supported multi-part questions into clauses and extracts time hints.",
    output: "Clauses to interpret, or a reason the request stops.",
    example: workedQuestion,
    limit: "The demo accepts English telemetry requests. Commands, forecasts and conditional joins are outside its scope."
  },
  {
    title: "NLP interpretation",
    input: "A clause, the selected mode and catalogue descriptions.",
    work: "Symbolic rules bind explicit IDs and aliases. MiniLM ranks eligible capability descriptions by cosine similarity. Hybrid uses MiniLM for unresolved clauses that remain eligible.",
    output: "The match method, candidate metrics and the recorded decision reason.",
    example: "In Symbolic mode, “CPU temperature” matches the processor temperature metric.",
    limit: "Ambiguity can stop the request. A cosine score measures similarity and does not express the probability of a correct interpretation."
  },
  {
    title: "Catalogue matching",
    input: "Candidates and the session’s capability catalogue.",
    work: "Entity and type constraints narrow the eligible metrics during interpretation. Registration determines which entries can be queried.",
    output: "Canonical metric IDs and their types, units and limits.",
    example: "P001 is a registered numeric metric in °C. P022 is the SoC voltage sensor and P170 is the VDD_CORE rail voltage.",
    limit: "Fictional manifests can change the current catalogue. Declared future metrics have no queryable history."
  },
  {
    title: "Typed query",
    input: "Selected metric IDs and the wording about time and operation.",
    work: "The planner combines the matched metric with time hints, the requested operation and any arguments in a typed query object.",
    output: "One or more typed queries and any assumptions.",
    example: "“Maximum” selects stats. “Today” resolves to 24h ending at the latest synthetic time.",
    limit: "Complex time expressions and some operation synonyms can be misread. The same stats operation covers min, max and mean."
  },
  {
    title: "Validate and compile",
    input: "Typed queries and the same runtime catalogue.",
    work: "Validation checks registration, type-compatible operations, time windows and required arguments. The compiler serializes accepted queries to exact tq lines.",
    output: "Validated query lines alongside the typed objects.",
    example: "Q 1 P001 24h stats",
    limit: "Validation checks the query contract. A valid query can still refer to the wrong metric if the question was misunderstood."
  },
  {
    title: "Synthetic retrieval",
    input: "Validated typed queries, the scenario, and current fictional manifests.",
    work: "The simulator generates the seeded history and computes the requested readings. It records gaps and the observed coverage of the window.",
    output: "Synthetic answer packets with values or states, timestamps and coverage.",
    example: "For P001 stats, the packet contains min, max, mean and n for the last 24 hours.",
    limit: "The scenario uses a fixed clock. It approximates the documented query operations and has its own sampling and gap rules."
  },
  {
    title: "Deterministic answer",
    input: "The simulator’s answer packet and metric metadata.",
    work: "The renderer formats those fields into a readable answer. The interface adds the exact query, source and coverage for inspection.",
    output: "A sourced answer and execution evidence.",
    example: "The P001 result shows °C, its sample count and completeness. The partial-history example shows zero observed events and the unobserved period.",
    limit: "The NLP selects metrics and builds queries. Values come from the simulator. A zero with partial coverage describes only the observed periods."
  }
];

function setupPipeline() {
  const modes = [
    ["Symbolic", "Explicit rules match known phrases and IDs to catalogue entries."],
    ["MiniLM", "An embedding model ranks eligible catalogue descriptions by similarity to the question. Explicit IDs can still use a symbolic match."],
    ["Hybrid", "The planner tries symbolic matching first and uses MiniLM for unresolved clauses. A guard can stop the request before a semantic search."]
  ];
  for (const [title, copy] of modes) {
    const card = element("article", undefined, "panel mode-card");
    card.append(element("h2", title), element("p", copy));
    $("#mode-explanations").append(card);
  }
  for (const stage of stages) {
    const step = element("li");
    const button = element("button", stage.title);
    button.type = "button";
    step.append(button);
    $("#pipeline-diagram").append(step);
    const details = element("details", undefined, "stage");
    const summary = element("summary", stage.title);
    details.append(summary, fieldList([
      ["Receives", stage.input], ["What it does", stage.work], ["Produces", stage.output],
      ["Example", stage.example], ["Limitations", stage.limit],
    ]));
    button.addEventListener("click", () => {
      details.open = true;
      summary.focus({preventScroll: true});
      details.scrollIntoView({block: "nearest"});
    });
    $("#pipeline-stages").append(details);
  }
  $("#run-worked-example").addEventListener("click", () => useExample(workedQuestion, "symbolic"));
}
