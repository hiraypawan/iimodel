"""Offline Flask smoke tests with assertions on response bodies.

No local model weights or external web requests are required. The chat route is
explicitly tested in demo mode so the test never claims that a real model ran.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app as app_module
from reasoning.ii_constraints import Constraint, ConstraintSharder
from graph.ii_graph import CodebaseKnowledgeGraph
from core.ii_state import StateManager
from core.llama_local import DummyModel, LMStudioModel

ROOT = Path(__file__).resolve().parents[1]


class AppSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state_tmp = tempfile.TemporaryDirectory()
        app_module.MODEL = DummyModel()
        app_module.KG = CodebaseKnowledgeGraph(str(ROOT))
        app_module.KG.build_graph()
        app_module.STATE = StateManager(cls.state_tmp.name)
        app_module.SHARDER = None
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.state_tmp.cleanup()
        app_module.MODEL = None
        app_module.KG = None
        app_module.STATE = None
        app_module.SHARDER = None

    def test_chat_page_and_demo_backend_status(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Local tool-augmented agent prototype", page.data)

        status = self.client.get("/api/status")
        self.assertEqual(status.status_code, 200)
        body = status.get_json()
        self.assertEqual(body["backend"], "demo")
        self.assertFalse(body["chat_ready"])
        self.assertIn("huggingface.co/Qwen/", body["setup_message"])
        self.assertEqual(body["version"], "3.0")
        self.assertIn("logic_tree", body["pipeline_stages"])
        self.assertIn("renderer", body["llm_role"])

    def test_pure_ii_chat_fails_closed_without_sources(self):
        # II_PRIVATE makes this deterministic and offline: the Pure II
        # pipeline must report that it could not verify anything instead of
        # inventing an answer.
        with patch.dict("os.environ", {"II_PRIVATE": "1"}):
            response = self.client.post(
                "/api/chat", json={"message": "What is the capital of France?"}
            )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "INSUFFICIENT_DATA")
        self.assertIn("No verified answer", body["response"])
        self.assertNotIn("As an AI", body["response"])
        self.assertEqual(body["sources"], [])

    def test_legacy_chat_reports_demo_instead_of_faking_model_output(self):
        # The legacy tool-calling path still reports demo mode rather than
        # inventing an answer or silently falling back to web search.
        with patch.dict("os.environ", {"II_PRIVATE": "1"}):
            response = self.client.post(
                "/api/chat", json={"message": "hello", "pipeline": "legacy"}
            )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["demo"])
        self.assertIn("no local model backend is available", body["response"])
        self.assertIn("LM Studio", body["response"])

    def test_empty_chat_is_rejected(self):
        response = self.client.post("/api/chat", json={"message": "  "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "empty message")

    def test_repository_index_and_state_endpoints(self):
        graph = self.client.get("/api/graph")
        self.assertEqual(graph.status_code, 200)
        self.assertIn("app.py", graph.get_json()["index"])

        topology = self.client.get("/api/graph/topology")
        self.assertEqual(topology.status_code, 200)
        self.assertIn("APPROXIMATE REPOSITORY TOPOLOGY", topology.get_json()["architecture_map"])

        blackboard_graph = self.client.get("/api/blackboard/graph")
        self.assertEqual(blackboard_graph.status_code, 200)
        self.assertTrue(blackboard_graph.get_json()["approximate"])

        state = self.client.get("/api/state")
        self.assertEqual(state.status_code, 200)
        self.assertIn("decision_context", state.get_json())

    def test_bounded_synthesis_content(self):
        response = self.client.post(
            "/api/synthesize",
            json={"examples": "[[[1, 2], 3], [[5, 7], 12]]"},
        )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()["result"]
        self.assertIn("MATCHING EXPRESSION (bounded search", result)
        self.assertIn("add(x0, x1)", result)

    def test_constraint_repair_accepts_unfenced_code(self):
        sharder = ConstraintSharder()
        sharder.add_constraint(
            Constraint(
                "no_bad_token",
                "remove the bad token",
                lambda code: ("bad" not in code, "contains bad"),
            )
        )
        fixed, results, rounds = sharder.enforce(
            "bad = 1", model_fix_fn=lambda _prompt, _code: "good = 1", max_rounds=2
        )
        self.assertEqual(fixed, "good = 1")
        self.assertTrue(results["no_bad_token"][0])
        self.assertEqual(rounds, 2)

    def test_build_requires_a_real_backend(self):
        response = self.client.post("/api/build", json={"task": "write a function"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("No language-model backend", response.get_json()["error"])

    def test_verification_api_reports_static_checks_only(self):
        response = self.client.post(
            "/api/verify",
            json={"code": "def greet():\n    return 'hello'", "required_symbols": ["greet"]},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["passed"])
        self.assertFalse(body["execution_performed"])
        self.assertFalse(body["sandboxed"])

        invalid = self.client.post("/api/verify", json={"code": "def broken(:"})
        self.assertEqual(invalid.status_code, 422)
        self.assertFalse(invalid.get_json()["passed"])

        cascade = self.client.post(
            "/api/verify", json={"code": "x = 1", "mode": "cascade"}
        )
        self.assertEqual(cascade.status_code, 422)
        self.assertEqual(cascade.get_json()["gates"][1]["status"], "skipped")

        invalid_mode = self.client.post(
            "/api/verify", json={"code": "x = 1", "mode": []}
        )
        self.assertEqual(invalid_mode.status_code, 400)
        invalid_requirements = self.client.post(
            "/api/verify", json={"code": "x = 1", "mode": "cascade", "requirements": "single"}
        )
        self.assertEqual(invalid_requirements.status_code, 400)

    def test_repository_ingestion_api_validates_and_uses_explicit_url(self):
        invalid = self.client.post(
            "/api/repos/ingest", json={"url": "https://example.com/owner/repo"}
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertFalse(invalid.get_json()["ingested"])
        self.assertIn("Only public https://github.com/OWNER/REPO", invalid.get_json()["error"])

        with patch(
            "ingestion.repo_ingestor.RepoIngestor.ingest",
            return_value="Ingested example/project (MIT; 2 supported files).",
        ) as ingest:
            response = self.client.post(
                "/api/repos/ingest", json={"url": "https://github.com/example/project"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ingested"])
        self.assertTrue(response.get_json()["graph_is_approximate"])
        ingest.assert_called_once_with("https://github.com/example/project")

    def test_repository_graph_api_queries_only_existing_cache(self):
        missing = self.client.get("/api/repos/graph")
        self.assertEqual(missing.status_code, 400)
        missing_cache = self.client.get("/api/repos/graph?repo=example/project")
        self.assertEqual(missing_cache.status_code, 404)

        with patch(
            "ingestion.repo_ingestor.RepoIngestor.query_graph",
            return_value='{"ranked_symbols": []}',
        ) as query:
            response = self.client.get(
                "/api/repos/graph?repo=example/project&target=main"
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["graph_is_approximate"])
        query.assert_called_once_with("example/project", "main")

    def test_steering_api_is_archived_and_never_claims_activation(self):
        listed = self.client.get("/api/steer")
        self.assertEqual(listed.status_code, 501)
        self.assertFalse(listed.get_json()["applied"])
        self.assertTrue(listed.get_json()["archived"])
        applied = self.client.post("/api/steer", json={"action": "apply", "name": "warm_empathetic"})
        self.assertEqual(applied.status_code, 501)
        self.assertFalse(applied.get_json()["applied"])

    def test_unsupported_vision_is_reported_as_not_implemented(self):
        response = self.client.post(
            "/api/vision",
            json={"image_path": "missing.png", "query": "button"},
        )
        self.assertEqual(response.status_code, 501)
        self.assertIn("prototype", response.get_json()["error"])

    def test_optional_z3_status_is_honest(self):
        response = self.client.post(
            "/api/smt",
            json={"problem": "VARIABLES: x Int\nCONSTRAINTS: x > 0"},
        )
        if app_module._SMT_AVAILABLE:
            self.assertEqual(response.status_code, 200)
            self.assertIn("SATISFIABLE", response.get_json()["result"])
        else:
            self.assertEqual(response.status_code, 503)
            self.assertIn("Z3 solver not available", response.get_json()["error"])

    def test_lm_studio_uses_the_model_id_reported_by_the_server(self):
        response = Mock(status_code=200)
        response.json.return_value = {"data": [{"id": "qwen-loaded-model"}]}
        with patch("core.llama_local.requests.get", return_value=response):
            backend = LMStudioModel()
        self.assertTrue(backend._available)
        self.assertEqual(backend.model_name, "qwen-loaded-model")

    def test_blackboard_list_endpoint(self):
        response = self.client.post("/api/blackboard", json={"action": "list"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("No locked interfaces", response.get_json()["result"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
