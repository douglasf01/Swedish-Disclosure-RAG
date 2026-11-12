"""The language model, as a LangChain chat model.

LangChain's chat-model interface is the seam between the agents and the model provider:
the agents only use `bind_tools`, `with_structured_output` and `invoke`, which every
provider's integration offers. Adding a provider is one more branch in `chat_model`.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from ..config import Settings

MAX_OUTPUT_TOKENS = 2000
MAX_RETRIES = 4  # for rate limits: the Gemini API's free tier allows few calls a minute


def chat_model(cfg: Settings) -> BaseChatModel:
    if cfg.llm_provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        # No temperature: current Gemini models use fixed sampling and warn when one is set.
        options = {
            "model": cfg.llm_model,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "max_retries": MAX_RETRIES,
        }
        if cfg.api_key:  # the Gemini API: has a free tier, handy for local runs
            return ChatGoogleGenerativeAI(google_api_key=cfg.api_key, **options)
        # Vertex AI, authenticated as the service account the code runs as.
        return ChatGoogleGenerativeAI(
            vertexai=True, project=cfg.project, location=cfg.location, **options
        )
    raise ValueError(f"unknown LLM_PROVIDER {cfg.llm_provider!r}. Add it in {__name__}")
