"""
llama_local.py — Local model backend (NO Ollama required)

Backend priority:
1. llama-cpp-python — loads GGUF directly
2. LM Studio — http://127.0.0.1:1234 (OpenAI-compatible API, auto-fallback)
3. DummyModel — pure-Python fallback (deterministic tools only)

v12: Auto LM Studio fallback eliminates Windows C++ Build Tools dependency.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

try:
    from llama_cpp import Llama
except ImportError:
    Llama = None  # type: ignore[assignment]


class LMStudioModel:
    """
    Connects to LM Studio's local server (default: http://127.0.0.1:1234).
    Start LM Studio → load a model → click "local server" → Start Server.
    API is OpenAI-compatible: POST http://127.0.0.1:1234/v1/chat/completions
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234",
        model_name: str = "local-model",
        timeout: int = 60,
    ):
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout = timeout
        self._available = self._check_available()

    def _check_available(self) -> bool:
        if requests is None:
            return False
        try:
            resp = requests.get(f"{self.base_url}/v1/models", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> dict:
        if not self._available:
            raise ConnectionError(f"LM Studio server not available at {self.base_url}")
        payload: dict = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            resp = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]
        except Exception as exc:
            raise ConnectionError(f"LM Studio request failed: {exc}")

    def complete(self, prompt: str, temperature: float = 0.2, max_tokens: int = 2048) -> str:
        return self.chat(
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        ).get("content", "")


class DummyModel:
    """
    Pure-Python fallback LLM. Used only when llama-cpp-python AND LM Studio
    are both unavailable. Demonstrates full ReAct architecture.
    """

    def __init__(
        self,
        model_path: str = "",
        n_ctx: int = 4096,
        n_threads: Optional[int] = None,
        n_gpu_layers: int = 0,
        verbose: bool = False,
    ):
        self.model_path = model_path
        self._mode = "demo"

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> dict:
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = m.get("content", "")
                break
        return {
            "content": (
                f"I received: '{user_text[:100]}'\n\n"
                "DEMO MODE: No real model backend connected.\n"
                "Install llama-cpp-python OR start LM Studio at http://127.0.0.1:1234\n"
                "for full chat capability."
            ),
            "tool_calls": [],
        }

    def complete(self, prompt: str, temperature: float = 0.2, max_tokens: int = 2048) -> str:
        return self.chat(
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        ).get("content", "")


class LocalModel:
    """
    Local model backend with automatic backend selection:

    1. llama-cpp-python — loads your .gguf file directly
    2. LM Studio — connects to http://127.0.0.1:1234 (no compiler needed)
    3. DummyModel — pure Python fallback (demonstrates full ReAct architecture)

    v12: Automatically falls back to LM Studio if llama-cpp-python is missing.
    """

    def __init__(
        self,
        model_path: str,
        n_ctx: int = 4096,
        n_threads: Optional[int] = None,
        n_gpu_layers: int = 0,
        verbose: bool = False,
        lmstudio_url: str = "http://127.0.0.1:1234",
    ):
        self.model_path = model_path
        self._mode = "demo"
        self._fallback = None

        # Try 1: llama-cpp-python (real GGUF model)
        if Llama is not None and os.path.exists(model_path):
            try:
                self.llm = Llama(
                    model_path=model_path,
                    n_ctx=n_ctx,
                    n_threads=n_threads,
                    n_gpu_layers=n_gpu_layers,
                    verbose=verbose,
                )
                self._mode = "gguf"
                print("[OK] Loaded GGUF model via llama-cpp-python")
                return
            except Exception as exc:
                print(f"[WARNING] llama-cpp-python failed: {exc}")

        # Try 2: LM Studio server (auto-fallback, no compilation needed)
        try:
            lm = LMStudioModel(base_url=lmstudio_url)
            if lm._available:
                self._lmstudio = lm
                self._mode = "lmstudio"
                print(f"[OK] Connected to LM Studio at {lmstudio_url}")
                return
        except Exception as exc:
            print(f"[WARNING] LM Studio not available: {exc}")

        # Try 3: DummyModel fallback
        self._fallback = DummyModel(model_path=model_path)
        self._mode = "demo"
        print("[INFO] Running in demo mode. Install llama-cpp-python or start LM Studio.")

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> dict:
        if self._mode == "gguf":
            return self.llm.create_chat_completion(
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )["choices"][0]["message"]

        if self._mode == "lmstudio":
            return self._lmstudio.chat(messages, tools, temperature, max_tokens)

        # demo mode
        return self._fallback.chat(messages, tools, temperature, max_tokens)

    def complete(self, prompt: str, temperature: float = 0.2, max_tokens: int = 2048) -> str:
        if self._mode == "gguf":
            resp = self.llm(prompt, temperature=temperature, max_tokens=max_tokens)
            return resp["choices"][0]["text"]

        if self._mode == "lmstudio":
            return self._lmstudio.complete(prompt, temperature, max_tokens)

        return self._fallback.complete(prompt, temperature, max_tokens)

    @property
    def mode(self) -> str:
        return self._mode


if __name__ == "__main__":
    import sys

    model_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        "models", "qwen2.5-1.5b-instruct-q4_0.gguf"
    )
    m = LocalModel(model_path)
    print(f"Mode: {m.mode}")

    tests = [
        "Hello, how are you?",
        "Solve x + y = 10 where x > 0 and y > 0",
        "Write a Python function that calculates factorial",
        "Search for Python tutorial",
        "What is the capital of France?",
    ]
    for t in tests:
        msg = m.chat([{"role": "user", "content": t}])
        tc = msg.get("tool_calls", [])
        print(f"\nQ: {t}")
        print(f"A: {msg.get('content', '')[:150]}...")
        if tc:
            print(f"  Tool: {tc[0]['function']['name']}")
