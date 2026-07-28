"""LiteLLM-backed chat and embeddings (any provider LiteLLM supports)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from litellm import completion, cost_per_token, embedding


@dataclass
class ModelUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_id: str | None = None

    def add(self, other: ModelUsage) -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        if other.model_id:
            self.model_id = other.model_id


@dataclass
class ChatResult:
    text: str
    usage: ModelUsage = field(default_factory=ModelUsage)


class RemoteModel:
    """Thin wrapper around LiteLLM ``completion`` / ``embedding``.

    Auth and provider routing are LiteLLM's job: set provider keys in the
    environment and choose a model id (``gpt-4o``, ``anthropic/claude-…``,
    ``openrouter/…``, ``ollama/llama3``, …). See https://docs.litellm.ai/
    """

    def __init__(
        self,
        *,
        chat_model: str | None = None,
        embed_model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.chat_model = chat_model or os.environ.get("FASTINDEX_MODEL") or ""
        self.embed_model = embed_model or os.environ.get("FASTINDEX_EMBED_MODEL") or ""
        self.timeout = timeout

    @property
    def available(self) -> bool:
        """True when a chat or embed model id is configured via env / args."""
        return bool(self.chat_model or self.embed_model)

    def chat(
        self, messages: list[dict[str, str]], *, temperature: float | None = 0.0
    ) -> ChatResult:
        if not self.chat_model:
            raise RuntimeError(
                "No chat model configured. Set FASTINDEX_MODEL to a LiteLLM "
                "model id and the matching provider API key (see .env.example)."
            )
        kwargs: dict = {
            "model": self.chat_model,
            "messages": messages,
            "timeout": self.timeout,
            "max_tokens": int(os.environ.get("FASTINDEX_MAX_TOKENS") or 1024),
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        last_err: Exception | None = None
        resp = None
        for attempt in range(8):
            try:
                resp = completion(**kwargs)
                break
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                # Some models only allow the provider default temperature.
                if temperature is not None and "temperature" in msg and attempt == 0:
                    kwargs.pop("temperature", None)
                    continue
                if "ratelimit" in type(e).__name__.lower() or "429" in str(e):
                    time.sleep(min(120.0, 8.0 * (attempt + 1)))
                    continue
                raise
        if resp is None:
            assert last_err is not None
            raise last_err

        text = resp.choices[0].message.content or ""
        usage_raw = getattr(resp, "usage", None)
        usage = ModelUsage(
            calls=1,
            input_tokens=int(getattr(usage_raw, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage_raw, "completion_tokens", 0) or 0),
            model_id=self.chat_model,
        )
        return ChatResult(text=text, usage=usage)

    def embed(self, texts: list[str]) -> tuple[list[list[float]], ModelUsage]:
        if not self.embed_model:
            raise RuntimeError(
                "No embed model configured. Set FASTINDEX_EMBED_MODEL to a "
                "LiteLLM model id and the matching provider API key "
                "(see .env.example)."
            )
        if not texts:
            return [], ModelUsage(model_id=self.embed_model)
        resp = embedding(
            model=self.embed_model,
            input=texts,
            timeout=self.timeout,
        )
        data = list(resp.data)
        data.sort(key=lambda item: item["index"] if isinstance(item, dict) else item.index)
        vectors = [
            list(item["embedding"] if isinstance(item, dict) else item.embedding) for item in data
        ]
        usage_raw = getattr(resp, "usage", None)
        usage = ModelUsage(
            calls=1,
            input_tokens=int(getattr(usage_raw, "prompt_tokens", 0) or 0),
            output_tokens=0,
            model_id=self.embed_model,
        )
        return vectors, usage


def estimate_cost_usd(input_tokens: int, output_tokens: int, model_id: str | None) -> float:
    """USD estimate via LiteLLM pricing tables; 0.0 if the model is unknown."""
    if not model_id:
        return 0.0
    call_type = "embedding" if "embed" in model_id.lower() else "completion"
    try:
        inp_c, out_c = cost_per_token(
            model=model_id,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            call_type=call_type,  # type: ignore[arg-type]
        )
        return float(inp_c) + float(out_c)
    except Exception:
        return 0.0
