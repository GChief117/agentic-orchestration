"""Portable text-model API adapters; API credentials never enter persisted state."""

import json
import os
from pathlib import Path
import re
import ssl
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "env": "OPENAI_API_KEY",
        "models": "https://api.openai.com/v1/models",
    },
    "claude": {
        "name": "Claude",
        "env": "ANTHROPIC_API_KEY",
        "models": "https://api.anthropic.com/v1/models?limit=100",
    },
    "gemini": {
        "name": "Gemini",
        "env": "GEMINI_API_KEY",
        "models": "https://generativelanguage.googleapis.com/v1beta/models?pageSize=100",
    },
}


def model_name(value):
    """Validate an explicit provider model identifier without choosing a fallback."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", value
    ):
        raise ValueError("Choose a valid model ID")
    return value


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        """Never forward credentials to a redirect destination."""
        raise URLError("Provider redirects are not allowed")


class ModelRuntime:
    """Validate credentials, list available models, and make bounded text requests."""

    def __init__(self, request=None):
        """Load optional environment keys into memory and allow fake transports in tests."""
        self.lock = threading.RLock()
        self.keys = {
            key: os.environ[info["env"]]
            for key, info in PROVIDERS.items()
            if os.environ.get(info["env"])
        }
        self.models = {}
        self.request = request or self._request

    def catalog(self):
        """Return safe connection metadata; never serialize a key or account token."""
        with self.lock:
            return [
                {
                    "id": key,
                    "name": info["name"],
                    "configured": bool(self.keys.get(key)),
                    "models": list(self.models.get(key, [])),
                }
                for key, info in PROVIDERS.items()
            ]

    def connect(self, provider, key=None):
        """Verify a supplied or environment key by listing models before retaining it."""
        self._provider(provider)
        with self.lock:
            credential = key if key is not None else self.keys.get(provider)
        if (
            not isinstance(credential, str)
            or not credential.strip()
            or len(credential) > 4096
            or any(c.isspace() for c in credential)
        ):
            raise ValueError("Enter a valid provider API key")
        payload = self.request(provider, credential, PROVIDERS[provider]["models"])
        if provider == "gemini":
            models = [
                item.get("name", "").removeprefix("models/")
                for item in payload.get("models", [])
                if "generateContent" in item.get("supportedGenerationMethods", [])
            ]
        else:
            models = [item.get("id", "") for item in payload.get("data", [])]
        models = sorted(
            {
                value
                for value in models
                if isinstance(value, str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", value)
            }
        )[:500]
        if not models:
            raise ValueError("This credential returned no available models")
        with self.lock:
            self.keys[provider] = credential
            self.models[provider] = models
        return self.catalog()

    def disconnect(self, provider):
        """Forget this process's key; an environment key reloads only on server restart."""
        self._provider(provider)
        with self.lock:
            self.keys.pop(provider, None)
            self.models.pop(provider, None)
        return self.catalog()

    def ready(self, provider):
        """Report whether the selected provider has a credential in this process."""
        with self.lock:
            return bool(self.keys.get(provider))

    def generate(self, provider, model, system, prompt, max_tokens):
        """Call the selected provider once, with no tools, retries, or provider fallback."""
        self._provider(provider)
        model = model_name(model)
        with self.lock:
            key = self.keys.get(provider)
        if not key:
            raise ValueError(
                "Connect the selected provider's API key before starting model tasks"
            )
        if provider == "openai":
            url = "https://api.openai.com/v1/responses"
            body = {
                "model": model,
                "instructions": system,
                "input": prompt,
                "max_output_tokens": max_tokens,
                "store": False,
            }
        elif provider == "claude":
            url = "https://api.anthropic.com/v1/messages"
            body = {
                "model": model,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            }
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model.removeprefix('models/'), safe='')}:generateContent"
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            }
        data = self.request(provider, key, url, body)
        if provider == "openai":
            text = "\n".join(
                part.get("text", "")
                for item in data.get("output", [])
                if item.get("type") == "message"
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            )
            complete = data.get("status") == "completed"
            usage = data.get("usage", {})
        elif provider == "claude":
            text = "\n".join(
                item.get("text", "")
                for item in data.get("content", [])
                if item.get("type") == "text"
            )
            complete = data.get("stop_reason") in ("end_turn", "stop_sequence")
            usage = data.get("usage", {})
        else:
            candidates = data.get("candidates", [])
            candidate = candidates[0] if candidates else {}
            text = "\n".join(
                part.get("text", "")
                for part in candidate.get("content", {}).get("parts", [])
                if not part.get("thought")
            )
            complete = candidate.get("finishReason") == "STOP"
            usage = data.get("usageMetadata", {})
        if not text.strip():
            raise ValueError("The provider returned no text output")
        return {
            "text": text,
            "complete": complete,
            "usage": usage,
            "response_id": data.get("id") or data.get("responseId"),
        }

    @staticmethod
    def _provider(provider):
        """Reject unknown providers before accessing credentials or making a request."""
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise ValueError("Choose OpenAI, Claude, or Gemini")

    @staticmethod
    def _request(provider, key, url, body=None):
        """Send bounded HTTPS JSON to fixed provider endpoints and redact remote errors."""
        headers = {"Content-Type": "application/json"}
        if provider == "gemini":
            headers["x-goog-api-key"] = key
        elif provider == "claude":
            headers.update({"x-api-key": key, "anthropic-version": "2026-10-21"})
        else:
            headers["Authorization"] = "Bearer " + key
        context = ssl.create_default_context()
        # macOS framework Python may omit the system CA path; retain TLS verification.
        if Path("/etc/ssl/cert.pem").is_file():
            context.load_verify_locations("/etc/ssl/cert.pem")
        opener = build_opener(NoRedirects(), HTTPSHandler(context=context))
        try:
            with opener.open(
                Request(
                    url,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers=headers,
                ),
                timeout=30,
            ) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("Provider response exceeded the size limit")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Provider returned an invalid response")
            return result
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise ValueError(
                f"Provider request failed (HTTP {code}); check credentials, model access, and quota"
            ) from None
        except (URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError(
                "Provider request failed; check the network and provider availability"
            ) from None
