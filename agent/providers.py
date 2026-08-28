"""LLM provider abstraction layer and mock implementations."""

import os
import json
import urllib.request
import urllib.error
from typing import Any


class LLMProvider:
    """Base class for LLM clients."""

    def generate(self, prompt: str, schema: dict[str, Any] | None = None) -> str:
        raise NotImplementedError


class MockLLMProvider(LLMProvider):
    """Deterministic LLM Mock provider for testing purposes."""

    def __init__(self, predefined_responses: dict[str, str] | None = None) -> None:
        self.predefined_responses = predefined_responses or {}

    def generate(self, prompt: str, schema: dict[str, Any] | None = None) -> str:
        # Search for keyword matches in the prompt to return appropriate mock decisions
        if "gateway_beta" in prompt or "GATEWAY_DEGRADATION" in prompt:
            return json.dumps({
                "reasoning": "Detected degradation on gateway_beta. Recommending a 50% traffic divert to gateway_alpha.",
                "selected_action": {
                    "action_type": "ROUTE_TRAFFIC",
                    "parameters": {
                        "source_gateway": "gateway_beta",
                        "destination_gateway": "gateway_alpha",
                        "traffic_percentage": 50.0,
                    },
                    "explanation": "Reroute 50% traffic away from unhealthy gateway_beta to gateway_alpha.",
                },
                "confidence": 0.95,
            })
        elif "CARD" in prompt or "CARD_AUTH_DEGRADATION" in prompt:
            return json.dumps({
                "reasoning": "Card authorization failure spike detected. Recommending card payment disablement.",
                "selected_action": {
                    "action_type": "DISABLE_PAYMENT_METHOD",
                    "parameters": {
                        "payment_method": "CARD",
                        "duration_minutes": 15,
                    },
                    "explanation": "Temporarily disable degraded CARD method to prevent customer retry lag.",
                },
                "confidence": 0.90,
            })
        elif "merchant_retail_001" in prompt:
            return json.dumps({
                "reasoning": "High volume merchant configuration failure. Recommending rate-limiting.",
                "selected_action": {
                    "action_type": "RATE_LIMIT_MERCHANT",
                    "parameters": {
                        "merchant": "merchant_retail_001",
                        "traffic_percentage": 50.0,
                    },
                    "explanation": "Apply 50% rate limiting to merchant retail.",
                },
                "confidence": 0.85,
            })

        # Default healthy response (no incident -> no action)
        return json.dumps({
            "reasoning": "System is completely healthy. No recovery action needed.",
            "selected_action": None,
            "confidence": 0.99,
        })


class RealLLMProvider(LLMProvider):
    """Real LLM provider implementing HTTP calls to Gemini or OpenAI."""

    def __init__(self, provider: str, api_key: str, model: str | None = None) -> None:
        self.provider = provider.lower()
        self.api_key = api_key
        if not model:
            if self.provider == "gemini":
                self.model = "gemini-1.5-flash"
            elif self.provider == "openai":
                self.model = "gpt-4o-mini"
            else:
                self.model = "default"
        else:
            self.model = model

    def generate(self, prompt: str, schema: dict[str, Any] | None = None) -> str:
        if self.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
            headers = {"Content-Type": "application/json"}
            data = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                }
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    res_body = response.read().decode("utf-8")
                    res_json = json.loads(res_body)
                    text = res_json["candidates"][0]["content"]["parts"][0]["text"]
                    return text
            except Exception as e:
                raise RuntimeError(f"Gemini API request failed: {e}")

        elif self.provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            data = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    res_body = response.read().decode("utf-8")
                    res_json = json.loads(res_body)
                    text = res_json["choices"][0]["message"]["content"]
                    return text
            except Exception as e:
                raise RuntimeError(f"OpenAI API request failed: {e}")

        else:
            raise ValueError(f"Unsupported provider: {self.provider}")


def get_llm_provider() -> LLMProvider:
    """Factory to fetch LLMProvider based on environment variables."""
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    api_key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", "")

    if provider == "mock":
        return MockLLMProvider()
    elif api_key:
        return RealLLMProvider(provider=provider, api_key=api_key, model=model)
    else:
        return MockLLMProvider()
