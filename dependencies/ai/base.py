"""
The interface every AI provider implements.

Deliberately one method, one job: text in, text out. Treatment guidance and
chat prompts are built once, provider-independently, in routers/diagnosis.py
and routers/chat.py - a provider never sees or knows about "treatment
guidance" vs "chat", only a prompt string and whether the caller wants JSON
back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AIProvider(ABC):
    #: Short, stable identifier used in config (AI_PROVIDER=...) and logs.
    name: str

    @abstractmethod
    def generate(self, prompt: str, *, json_mode: bool = False) -> str:
        """Runs `prompt` and returns the text response.

        `json_mode` asks the provider to bias toward returning a JSON
        object when it supports doing so; every prompt that sets it ALSO
        states the exact JSON schema in the prompt text itself, so a
        provider that ignores the hint still has a real shot at a
        parseable answer.

        Raises only the types in dependencies.ai.errors - see that module
        for what each means and how a caller should react.
        """
