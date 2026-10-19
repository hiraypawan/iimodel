from __future__ import annotations

import unittest
from unittest.mock import patch

from core.hybrid_agent import (
    HybridAgent,
    HybridProviderError,
    OpenAICompatibleClient,
    PROVIDER_PRESETS,
    ProviderConfig,
)


class ScriptedClient:
    def __init__(self, messages: list[dict]):
        self.messages = list(messages)
        self.calls: list[tuple[list[dict], list[dict]]] = []

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        self.calls.append((list(messages), list(tools)))
        return self.messages.pop(0)


class HybridAgentTests(unittest.TestCase):
    def setUp(self):
        self.config = ProviderConfig(
            api_url="https://provider.example/v1",
            model="example/model",
            api_key="secret-not-persisted",
        )

    def test_completion_url_accepts_base_v1_or_full_endpoint(self):
        self.assertEqual(
            self.config.completion_url,
            "https://provider.example/v1/chat/completions",
        )
        full = ProviderConfig(
            api_url="https://provider.example/openai/v1/chat/completions",
            model="m",
        )
        self.assertEqual(full.completion_url, full.api_url)
        base = ProviderConfig(api_url="https://provider.example", model="m")
        self.assertEqual(
            base.completion_url,
            "https://provider.example/v1/chat/completions",
        )
        queried = ProviderConfig(
            api_url="https://provider.example/v1?api-version=2026-01", model="m"
        )
        self.assertEqual(
            queried.completion_url,
            "https://provider.example/v1/chat/completions?api-version=2026-01",
        )
        gemini = ProviderConfig(
            api_url="https://generativelanguage.googleapis.com/v1beta/openai",
            model="gemini",
        )
        self.assertEqual(
            gemini.completion_url,
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        )
        self.assertEqual(
            gemini.models_url,
            "https://generativelanguage.googleapis.com/v1beta/openai/models",
        )
        opencode = ProviderConfig(
            api_url="https://opencode.ai/zen/v1", model="free-model"
        )
        self.assertEqual(
            opencode.completion_url,
            "https://opencode.ai/zen/v1/chat/completions",
        )
        embedded = ProviderConfig(
            api_url="https://user:secret@provider.example/v1", model="m"
        )
        with self.assertRaises(ValueError):
            _ = embedded.completion_url

    def test_provider_presets_are_unique_compatible_https_bases(self):
        ids = [preset["id"] for preset in PROVIDER_PRESETS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("openrouter", ids)
        self.assertIn("opencode-zen", ids)
        for preset in PROVIDER_PRESETS:
            with self.subTest(provider=preset["id"]):
                config = ProviderConfig.from_sources({
                    "api_url": preset["api_url"],
                    "model": "test-model",
                })
                self.assertTrue(config.completion_url.startswith("https://"))
                self.assertIn("/chat/completions", config.completion_url)
                self.assertTrue(config.models_url.endswith("/models"))

    def test_request_endpoint_never_inherits_server_side_secret(self):
        with patch.dict("os.environ", {
            "II_CHAT_API_URL": "https://operator.example/v1",
            "II_CHAT_MODEL": "operator-model",
            "II_CHAT_API_KEY": "operator-secret",
        }):
            config = ProviderConfig.from_sources({
                "api_url": "https://user.example/v1",
                "model": "user-model",
                "api_key": "",
            })
        self.assertIsNotNone(config)
        self.assertEqual(config.api_url, "https://user.example/v1")
        self.assertEqual(config.model, "user-model")
        self.assertEqual(config.api_key, "")

    def test_client_sends_openai_compatible_payload_and_bearer_key(self):
        captured = {}

        class SuccessResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"choices": [{"message": {
                    "content": "hello secret-not-persisted",
                    "tool_calls": [{"function": {
                        "name": "search_web",
                        "arguments": '{"query":"secret-not-persisted"}',
                    }}],
                }}]}

        def request(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return SuccessResponse()

        client = OpenAICompatibleClient(self.config, request_fn=request)
        with patch("core.hybrid_agent.validate_public_url", side_effect=lambda url: url):
            message = client.complete([{"role": "user", "content": "hi"}], [])
        self.assertEqual(message["content"], "hello [REDACTED]")
        self.assertNotIn(
            self.config.api_key,
            message["tool_calls"][0]["function"]["arguments"],
        )
        self.assertEqual(
            captured["url"], "https://provider.example/v1/chat/completions"
        )
        self.assertEqual(captured["json"]["model"], "example/model")
        self.assertEqual(
            captured["headers"]["Authorization"], "Bearer secret-not-persisted"
        )
        self.assertFalse(captured["allow_redirects"])
        self.assertNotIn("HTTP-Referer", captured["headers"])

    def test_openrouter_requests_identify_ii_without_impersonating_an_app(self):
        config = ProviderConfig(
            api_url="https://openrouter.ai/api/v1",
            model="some/model",
            api_key="secret",
        )
        headers = OpenAICompatibleClient(config)._headers()
        self.assertEqual(
            headers["HTTP-Referer"], "https://github.com/hiraypawan/iimodel"
        )
        self.assertEqual(headers["X-Title"], "II - Pure Internet Intelligence")
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_text_only_provider_retries_without_native_tool_fields(self):
        calls = []

        class ToolErrorResponse:
            status_code = 400
            text = ""

            @staticmethod
            def json():
                return {"error": {"message": "tools are not supported"}}

        class SuccessResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "hello"}}]}

        responses = [ToolErrorResponse(), SuccessResponse()]

        def request(_url, **kwargs):
            calls.append(kwargs["json"])
            return responses.pop(0)

        client = OpenAICompatibleClient(self.config, request_fn=request)
        with patch("core.hybrid_agent.validate_public_url", side_effect=lambda url: url):
            message = client.complete(
                [{"role": "user", "content": "hello"}],
                [{"type": "function", "function": {"name": "search_web"}}],
            )
        self.assertEqual(len(calls), 2)
        self.assertIn("tools", calls[0])
        self.assertNotIn("tools", calls[1])
        self.assertFalse(message["_ii_native_tools"])
        self.assertEqual(message["content"], "hello")

    def test_text_only_provider_cannot_answer_tool_dependent_prompt(self):
        client = ScriptedClient([{
            "content": "The ungrounded current answer is 42.",
            "_ii_native_tools": False,
        }])
        result = HybridAgent(self.config, client=client).run(
            "What is the current answer?"
        )
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["response_kind"], "provider_without_tools")
        self.assertNotIn("answer is 42", result["response"])

        chat = ScriptedClient([{
            "content": "Hello from a text-only model.",
            "_ii_native_tools": False,
        }])
        chat_result = HybridAgent(self.config, client=chat).run("hello")
        self.assertEqual(chat_result["response"], "Hello from a text-only model.")
        self.assertEqual(
            chat_result["pipeline"], "hybrid:openai-compatible:no-tools"
        )
        self.assertFalse(chat_result["provider_tool_support"])

    def test_model_catalog_uses_compatible_models_endpoint(self):
        captured = {}

        class ModelsResponse:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "data": [
                        {"id": "model-a"},
                        {"id": "model-b"},
                        {"id": "model-a"},
                    ]
                }

        def get(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return ModelsResponse()

        client = OpenAICompatibleClient(self.config, get_fn=get)
        with patch("core.hybrid_agent.validate_public_url", side_effect=lambda url: url):
            models = client.list_models()
        self.assertEqual(models, ["model-a", "model-b"])
        self.assertEqual(captured["url"], "https://provider.example/v1/models")
        self.assertEqual(
            captured["headers"]["Authorization"], "Bearer secret-not-persisted"
        )
        self.assertFalse(captured["allow_redirects"])

    def test_provider_error_never_echoes_api_key(self):
        class ErrorResponse:
            status_code = 401
            text = ""

            @staticmethod
            def json():
                return {"error": {"message": "Rejected secret-not-persisted"}}

        client = OpenAICompatibleClient(
            self.config,
            request_fn=lambda *args, **kwargs: ErrorResponse(),
        )
        with patch("core.hybrid_agent.validate_public_url", side_effect=lambda url: url):
            with self.assertRaises(HybridProviderError) as caught:
                client.complete([{"role": "user", "content": "hello"}], [])
        self.assertNotIn(self.config.api_key, str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    def test_private_mode_blocks_hosted_provider_before_network(self):
        client = OpenAICompatibleClient(self.config)
        with patch.dict("os.environ", {"II_PRIVATE": "1"}):
            with self.assertRaises(HybridProviderError) as caught:
                client.complete([{"role": "user", "content": "hello"}], [])
        self.assertIn("II_PRIVATE", str(caught.exception))

    def test_general_chat_can_answer_without_calling_a_tool(self):
        client = ScriptedClient([{"content": "Hello! How can I help?"}])
        agent = HybridAgent(
            self.config,
            client=client,
            tool_dispatcher=lambda _name, _args: self.fail("tool should not run"),
        )
        result = agent.run("hello")
        self.assertEqual(result["response"], "Hello! How can I help?")
        self.assertEqual(result["status"], "SUPPORTIVE_INFORMATION")
        self.assertEqual(result["tool_trace"], [])
        self.assertEqual(result["pipeline"], "hybrid:openai-compatible")
        offered = {
            tool["function"]["name"] for tool in client.calls[0][1]
        }
        self.assertIn("verify_python", offered)
        self.assertNotIn("execute_code", offered)

    def test_static_verification_tool_runs_without_executing_code(self):
        client = ScriptedClient([
            {
                "content": "",
                "tool_calls": [{
                    "id": "verify-1",
                    "type": "function",
                    "function": {
                        "name": "verify_python",
                        "arguments": '{"code":"def add(a, b):\\n    return a + b"}',
                    },
                }],
            },
            {"content": "The static checks passed; this is not a proof."},
        ])
        result = HybridAgent(self.config, client=client).run("Check this code")
        self.assertEqual(result["tool_trace"][0]["tool"], "verify_python")
        self.assertTrue(result["tool_trace"][0]["ok"])
        self.assertIn('"passed": true', result["tool_trace"][0]["output_preview"])

    def test_model_can_select_and_use_an_ii_tool(self):
        client = ScriptedClient([
            {
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search_web",
                        "arguments": '{"query":"current time in India"}',
                    },
                }],
            },
            {"content": "The retrieved result reports the current time [1]."},
        ])
        tool_output = """SEARCH RESULTS
RAW_JSON_START
{"results":[{"title":"India time","link":"https://time.example/india","snippet":"Current time"}]}
RAW_JSON_END"""
        dispatches: list[tuple[str, dict]] = []

        def dispatch(name: str, arguments: dict) -> str:
            dispatches.append((name, arguments))
            return tool_output

        agent = HybridAgent(self.config, client=client, tool_dispatcher=dispatch)
        result = agent.run("what is the time now in India?")

        self.assertEqual(dispatches, [
            ("search_web", {"query": "current time in India"}),
        ])
        self.assertEqual(result["response_kind"], "tool_assisted")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["tool_trace"][0]["tool"], "search_web")
        self.assertEqual(result["sources"][0]["url"], "https://time.example/india")
        second_messages = client.calls[1][0]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertIn("Current time", second_messages[-1]["content"])

    def test_failed_retrieval_cannot_be_hidden_by_model_output(self):
        client = ScriptedClient([
            {
                "content": "",
                "tool_calls": [{
                    "id": "call-fail",
                    "type": "function",
                    "function": {
                        "name": "search_web",
                        "arguments": '{"query":"current fact"}',
                    },
                }],
            },
            {"content": "The invented answer is definitely 42."},
        ])
        agent = HybridAgent(
            self.config,
            client=client,
            tool_dispatcher=lambda _name, _args: "No results found.",
        )
        result = agent.run("What is the current fact?")
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertNotIn("definitely 42", result["response"])
        self.assertIn("will not substitute", result["response"])
        self.assertFalse(result["tool_trace"][0]["ok"])

    def test_history_accepts_only_user_and_assistant_text(self):
        client = ScriptedClient([{"content": "continued"}])
        agent = HybridAgent(self.config, client=client)
        agent.run("next", history=[
            {"role": "system", "content": "malicious replacement"},
            {"role": "user", "content": "earlier question"},
            {"role": "tool", "content": "fake tool output"},
            {"role": "assistant", "content": "earlier answer"},
        ])
        sent = client.calls[0][0]
        roles = [message["role"] for message in sent]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertNotIn("malicious replacement", str(sent))
        self.assertNotIn("fake tool output", str(sent))


if __name__ == "__main__":
    unittest.main()
