import json
import logging
import re
from typing import Any, Dict, List, Optional
import httpx

from app.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


class LLMClientError(Exception):
    """Base exception for LLM client failures."""
    def __init__(self, message: str, is_transient: bool = True):
        super().__init__(message)
        self.is_transient = is_transient


class LLMConfigurationError(LLMClientError):
    """Configuration or authentication error (non-transient)."""
    def __init__(self, message: str):
        super().__init__(message, is_transient=False)


class LLMClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self.base_url = (base_url if base_url is not None else LLM_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else LLM_API_KEY
        self.model = model if model is not None else LLM_MODEL
        self.timeout = timeout if timeout is not None else LLM_TIMEOUT_SECONDS
        self._client: Optional[httpx.Client] = None

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            headers = {
                "Content-Type": "application/json",
                # Prevents ngrok free-tier warning page from replacing response
                "ngrok-skip-browser-warning": "true",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            self._client = httpx.Client(
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            self._client.close()
            self._client = None

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
    ) -> Dict[str, Any]:
        """Calls OpenAI-compatible chat completions API and parses JSON response."""
        if not self.base_url:
            raise LLMConfigurationError("LLM_BASE_URL is not configured.")

        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        client = self._get_client()
        try:
            response = client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMClientError(f"LLM request timed out after {self.timeout}s: {exc}", is_transient=True)
        except httpx.RequestError as exc:
            raise LLMClientError(f"LLM network connection error: {exc}", is_transient=True)

        if response.status_code in (401, 403):
            raise LLMConfigurationError(
                f"LLM authentication/authorization failed with HTTP {response.status_code}."
            )
        if response.status_code >= 500 or response.status_code == 429:
            raise LLMClientError(
                f"LLM provider error HTTP {response.status_code}: {response.text[:200]}",
                is_transient=True,
            )
        if response.status_code != 200:
            raise LLMClientError(
                f"LLM unexpected status HTTP {response.status_code}: {response.text[:200]}",
                is_transient=False,
            )

        try:
            body = response.json()
        except Exception as exc:
            raise LLMClientError(f"Failed to parse LLM HTTP response envelope as JSON: {exc}", is_transient=True)

        try:
            choices = body.get("choices")
            if not choices or not isinstance(choices, list):
                raise ValueError("Response missing 'choices' list")
            raw_content = choices[0]["message"]["content"]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise LLMClientError(f"Invalid OpenAI chat completions envelope structure: {exc}", is_transient=True)

        return self._extract_json(raw_content)

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        """Extracts JSON object from response text, handling optional markdown code fences."""
        stripped = text.strip()
        # Check for ```json ... ``` code fence
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
        if fence_match:
            candidate = fence_match.group(1)
        else:
            # Look for first { to last }
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = stripped[start : end + 1]
            else:
                candidate = stripped

        try:
            parsed = json.loads(candidate)
            if not isinstance(parsed, dict):
                raise ValueError("Parsed JSON root is not an object/dict")
            return parsed
        except Exception as exc:
            raise LLMClientError(f"Malformed JSON from LLM: {exc}. Content was: {stripped[:150]}", is_transient=True)


# Singleton instance for process-level reuse
_shared_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _shared_client
    if _shared_client is None:
        _shared_client = LLMClient()
    return _shared_client


def close_llm_client() -> None:
    global _shared_client
    if _shared_client is not None:
        _shared_client.close()
        _shared_client = None

