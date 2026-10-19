"""Tests for II_PRIVATE mode, the SSRF guard, and code-execution modes."""

from __future__ import annotations

import os
import socket
import unittest
from unittest.mock import patch

import core.ii_security as ii_security
from core.ii_security import (
    is_private_mode,
    run_python_code,
    sandbox_mode,
    validate_public_url,
)


def _fake_resolver(address: str):
    def resolve(host, port, proto=0):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, port))]

    return resolve


class PrivateModeTests(unittest.TestCase):
    def test_truthy_and_falsy_values(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            with patch.dict(os.environ, {"II_PRIVATE": value}):
                self.assertTrue(is_private_mode(), value)
        for value in ("0", "false", "no", "off", ""):
            with patch.dict(os.environ, {"II_PRIVATE": value}):
                self.assertFalse(is_private_mode(), value)

    def test_web_tools_blocked_without_network(self):
        import ingestion.ii_web as ii_web

        with patch.dict(os.environ, {"II_PRIVATE": "1"}):
            self.assertIn("II_PRIVATE", ii_web.search_web("anything"))
            self.assertIn("II_PRIVATE", ii_web.read_url("https://example.org/"))
            report = ii_web.ResearchEngine().research("anything")
            self.assertEqual(report["total_sources"], 0)
            self.assertIn("II_PRIVATE", report["error"])
            fetched = ii_web.fetch_url_metadata("https://example.org/")
            self.assertFalse(fetched.success)

    def test_repo_ingestion_refused_in_private_mode(self):
        from ingestion.repo_ingestor import RepoIngestor

        with patch.dict(os.environ, {"II_PRIVATE": "true"}):
            with self.assertRaises(PermissionError):
                RepoIngestor().ingest("https://github.com/example/example")

    def test_flask_network_routes_return_403_in_private_mode(self):
        import app as app_module

        client = app_module.app.test_client()
        with patch.dict(os.environ, {"II_PRIVATE": "1"}):
            for path, payload in (
                ("/api/search", {"query": "x"}),
                ("/api/fetch", {"url": "https://example.org/"}),
                ("/api/research", {"query": "x"}),
                ("/api/providers/models", {
                    "provider": {"api_url": "https://example.org/v1"}
                }),
                ("/api/repos/ingest", {"url": "https://github.com/a/b"}),
            ):
                response = client.post(path, json=payload)
                self.assertEqual(response.status_code, 403, path)
            status = client.get("/api/status").get_json()
            self.assertTrue(status["private_mode"])
            self.assertFalse(status["web_tools_enabled"])


class UrlValidationTests(unittest.TestCase):
    def test_public_url_accepted_with_public_resolution(self):
        url = validate_public_url(
            "https://example.org/path#fragment",
            resolver=_fake_resolver("93.184.216.34"),
        )
        self.assertEqual(url, "https://example.org/path")

    def test_literal_private_and_metadata_addresses_rejected(self):
        for url in (
            "http://127.0.0.1/admin",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "https://localhost:5000/",
            "https://metadata.google.internal/",
        ):
            with self.assertRaises(ValueError, msg=url):
                validate_public_url(url)

    def test_hostname_resolving_to_private_address_rejected(self):
        with self.assertRaises(ValueError):
            validate_public_url(
                "https://evil.example/",
                resolver=_fake_resolver("192.168.0.9"),
            )

    def test_schemes_credentials_and_empty_urls_rejected(self):
        for url in ("ftp://example.org/", "file:///etc/passwd", "https://user:pass@example.org/", ""):
            with self.assertRaises(ValueError, msg=url):
                validate_public_url(url, resolver=_fake_resolver("93.184.216.34"))

    def test_read_url_reports_blocked_url_without_fetching(self):
        import ingestion.ii_web as ii_web

        result = ii_web.read_url("http://127.0.0.1:5000/api/state")
        self.assertIn("blocked URL", result)


class CodeExecutionTests(unittest.TestCase):
    def test_subprocess_mode_runs_code_and_reports_errors(self):
        ok, output = run_python_code("print('hello-ii')")
        self.assertTrue(ok)
        self.assertIn("hello-ii", output)
        ok, output = run_python_code("raise ValueError('boom')")
        self.assertFalse(ok)
        self.assertIn("boom", output)

    def test_subprocess_mode_scrubs_environment(self):
        with patch.dict(os.environ, {"II_TEST_SECRET": "should-not-leak"}):
            ok, output = run_python_code(
                "import os; print(os.environ.get('II_TEST_SECRET', 'absent'))"
            )
        self.assertTrue(ok)
        self.assertIn("absent", output)

    def test_require_sandbox_refuses_subprocess_mode(self):
        with patch.dict(os.environ, {"II_REQUIRE_SANDBOX": "1", "II_CODE_SANDBOX": "subprocess"}):
            ok, output = run_python_code("print('nope')")
        self.assertFalse(ok)
        self.assertIn("II_REQUIRE_SANDBOX", output)

    def test_docker_mode_fails_closed_without_docker(self):
        with patch.dict(os.environ, {"II_CODE_SANDBOX": "docker"}):
            self.assertEqual(sandbox_mode(), "docker")
            with patch.object(ii_security.shutil, "which", return_value=None):
                ok, output = run_python_code("print('nope')")
        self.assertFalse(ok)
        self.assertIn("Failing closed", output)

    def test_unknown_sandbox_mode_fails_closed(self):
        with patch.dict(os.environ, {"II_CODE_SANDBOX": "magic"}):
            ok, output = run_python_code("print('nope')")
        self.assertFalse(ok)
        self.assertIn("Unknown II_CODE_SANDBOX", output)


if __name__ == "__main__":
    unittest.main()
