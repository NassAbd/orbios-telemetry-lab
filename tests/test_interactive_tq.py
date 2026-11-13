"""Known synthetic facts and fail-closed behavior, separate from the evaluation set."""
import unittest
from pathlib import Path

from pydantic import ValidationError

from orbios_qa_bench.interactive_tq.contract import Candidate, Manifest
from orbios_qa_bench.interactive_tq.planner import Planner
from orbios_qa_bench.interactive_tq.presentation import LAB_REQUEST, Lab
from orbios_qa_bench.interactive_tq.service import Service
from orbios_qa_bench.tq_contract import OPS, parse_query

ROOT = Path(__file__).parents[1]


class InteractiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = Service(ROOT, enable_semantic=False)

    def test_known_samples_and_threshold_bounds(self) -> None:
        queries = (
            ("Q 1 P001 1m stats", {"n": "6", "min": "58", "max": "72", "mean": "66"}),
            ("Q 2 P001 1m above 65", {"hits": "3", "secs": "30", "first": "-40", "last": "-20"}),
            ("Q 3 P001 1m series 3", {"s": "65000,72000,61000", "scale": "0.001"}),
        )
        service = self.service
        for line, expected in queries:
            reading = service.simulator.read(parse_query(line, service.catalog), service.catalog, service.apps)
            self.assertEqual(reading.completeness, "complete")
            self.assertEqual({k: reading.packet.fields[k] for k in expected}, expected)
        coarse = service.simulator.read(parse_query("Q 1 P001 2h above 65", service.catalog), service.catalog, service.apps)
        self.assertEqual(coarse.packet.fields["res"], "60")
        self.assertGreaterEqual(int(coarse.packet.fields["secs_le"]), int(coarse.packet.fields["hits"]) * 10)
        self.assertNotIn("secs", coarse.packet.fields)

    def test_missing_observation_is_not_zero_evidence(self) -> None:
        reading = self.service.ask("How many false alerts did oil leak report in the last hour?", "symbolic").readings[0]
        self.assertEqual(reading.packet.fields["n"], "0")
        self.assertEqual(reading.packet.fields["gap"], "1200")
        self.assertEqual(reading.completeness, "partial")
        self.assertIn("Unobserved periods", reading.text)
        latest = self.service.ask("What is the input voltage right now?", "symbolic").readings[0]
        self.assertEqual((latest.packet.freshness, latest.completeness), ("stale", "nodata"))
        self.assertEqual(latest.packet.fields["age"], "90")
        series = self.service.ask("Show a board power graph with 6 bins in the last hour", "symbolic").readings[0]
        self.assertEqual(series.completeness, "partial")
        self.assertIn("-", series.packet.fields["s"].split(","))

    def test_entity_binding_and_no_incomplete_answers(self) -> None:
        service = self.service
        reply = service.ask("Is ship detection running now?", "hybrid")
        self.assertEqual(reply.lines, ["Q 1 A.ship_detection.job 1h transitions"])
        self.assertEqual(reply.semantic_calls, 0)
        ambiguous = service.ask("What is the voltage?", "symbolic")
        self.assertEqual(ambiguous.translation.status, "clarification_required")
        self.assertFalse(ambiguous.lines)
        mixed = service.ask("What is the temperature and how fast is the satellite moving?", "symbolic")
        self.assertFalse(mixed.lines)
        self.assertEqual(service.ask("Restart oil leak", "symbolic").translation.status, "out_of_scope")
        self.assertEqual(service.ask("Give me 61 bins of board power in the last hour", "symbolic").translation.status, "clarification_required")

    def test_semantic_margin_and_unresolved_clauses(self) -> None:
        service = self.service
        calls = []

        def rank(question: str, pool: list[str]) -> list[Candidate]:
            calls.append(question)
            self.assertIn("P001", pool)
            return [Candidate(id="P001", score=0.71), Candidate(id="P002", score=0.69)]

        planner = Planner(service.catalog, service.apps, service.simulator.now, rank)
        result, _, _, n = planner.plan("Show board power and chip warmth in the last hour", "hybrid")
        self.assertEqual(calls, ["chip warmth in the last hour"])
        self.assertEqual(n, 1)
        self.assertEqual(result.status, "clarification_required")
        self.assertFalse(result.queries)

    def test_manifest_can_add_custom_metric_but_not_commands(self) -> None:
        service = self.service
        payload = {"id": "demo_probe", "label": "Demo probe", "version": "1.0", "budget_s": 100,
                   "metrics": [{"id": "glint", "label": "Solar glint", "kind": "event"}]}
        with self.assertRaises(ValidationError):
            Manifest.model_validate({**payload, "command": "anything"})
        app = Manifest.model_validate(payload)
        service.save(app)
        reply = service.ask("How many solar glint events did demo probe record today?", "symbolic")
        self.assertEqual(reply.lines, ["Q 1 A.demo_probe.glint 24h count"])
        self.assertEqual(reply.readings[0].packet.fields["ver"], "1.0")
        service.remove(app.id)
        self.assertFalse(service.ask("How many solar glint events from app demo_probe today?", "symbolic").lines)

    def test_last_before_window_and_score_counts(self) -> None:
        service = self.service
        q = parse_query("Q 1 A.ship_detection.detected 1m last", service.catalog)
        reading = service.simulator.read(q, service.catalog, service.apps)
        self.assertEqual(reading.packet.fields["at"], "-180")
        self.assertIn("precedes the requested window", reading.text)
        scores = service.ask("What were the confidence scores of ship detection events in the last hour?", "symbolic").readings[0]
        self.assertEqual((scores.packet.fields["n"], scores.packet.fields["scored"]), ("2", "1"))

    def test_runtime_catalogue_groups_origin_and_operations(self) -> None:
        lab = Lab(ROOT, self.service)
        rows = {mid: lab.metadata(p) for mid, p in self.service.catalog.items()}
        self.assertEqual(len(rows), 63)
        for mid, parameter in self.service.catalog.items():
            self.assertEqual(rows[mid]["operations"], sorted(OPS[parameter.kind]))
            self.assertEqual(rows[mid]["registered"], parameter.registered)
        self.assertEqual(rows["P022"]["group"], "Pi power supply")
        self.assertEqual(rows["P170"]["group"], "Power rails")
        self.assertEqual(rows["P010"]["documented_cadence_s"], 60)
        self.assertEqual(rows["A.ship_detection.job"]["application_version"], "1.0-sim")
        self.assertEqual(rows["A.ship_detection.job"]["origin"], "Derived from Ben’s guide")
        self.assertIsNone(rows["A.ship_detection.turn_used_s"]["documented_cadence_s"])
        self.assertEqual(rows["A.aurora_watch.glow_index"]["origin"], "Fictional manifest")
        self.assertEqual(rows["A.wildfire.alert"]["group"], "Declared but unavailable")
        self.assertIsNone(lab.detail("A.wildfire.alert")["example"])

    def test_history_preview_uses_validated_query_and_actual_coverage(self) -> None:
        lab = Lab(ROOT, self.service)
        p = lab.detail("A.oil_leak.false_alert")
        self.assertEqual(p["example"]["line"], "Q 1 A.oil_leak.false_alert 1h count")
        self.assertEqual(p["preview"]["packet"]["fields"]["n"], "0")
        self.assertEqual(p["preview"]["packet"]["fields"]["gap"], "1200")
        self.assertEqual(p["preview"]["completeness"], "partial")
        self.assertEqual(lab.detail("P011")["preview"]["completeness"], "nodata")

    def test_lab_explanation_describes_the_same_execution(self) -> None:
        lab = Lab(ROOT, self.service)
        question = "What's the maximum CPU temperature today?"
        result = lab.dispatch(LAB_REQUEST.validate_python({"action": "ask", "question": question, "mode": "symbolic"}))
        original = self.service.ask(question, "symbolic").model_dump(exclude={"elapsed_ms"})
        self.assertEqual({k: result[k] for k in original}, original)
        self.assertEqual(result["question"], question)
        self.assertEqual(result["catalogue_revision"], 1)
        self.assertEqual(result["lines"], ["Q 1 P001 24h stats"])
        self.assertEqual(result["metrics"]["P001"]["unit"], "°C")
        rejected = lab.dispatch(LAB_REQUEST.validate_python({"action": "ask", "question": "Restart oil leak"}))
        self.assertEqual(rejected["translation"]["status"], "out_of_scope")
        self.assertEqual(rejected["decisions"], [])
        self.assertEqual(rejected["metrics"], {})

    def test_catalogue_detail_tracks_manifest_edit_and_removal(self) -> None:
        lab = Lab(ROOT, self.service)
        app = Manifest.model_validate({
            "id": "test_probe", "label": "Test probe", "version": "1.0-sim", "budget_s": 60,
            "metrics": [{"id": "pulse", "label": "Pulse", "kind": "event"}],
        })
        self.service.save(app)
        mid = "A.test_probe.pulse"
        self.assertEqual(lab.detail(mid)["group"], "Fictional custom application metrics")
        self.assertEqual(lab.detail(mid)["revision"], 2)
        self.service.save(app.model_copy(update={"version": "1.1-sim", "metrics": [
            app.metrics[0].model_copy(update={"registered": False}),
        ]}))
        detail = lab.detail(mid)
        self.assertEqual(detail["application_version"], "1.1-sim")
        self.assertFalse(detail["registered"])
        self.assertIsNone(detail["preview"])
        self.service.remove("test_probe")
        with self.assertRaisesRegex(ValueError, "absent"):
            lab.detail(mid)
