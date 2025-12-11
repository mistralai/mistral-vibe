"""ChefChat Kitchen Brain - LLM Integration Layer.

The Brain is the intelligence behind the kitchen operations.
Supports multiple LLM providers: OpenAI and Anthropic.

Usage:
    from chefchat.kitchen.brain import KitchenBrain
    brain = KitchenBrain()
    plan = await brain.generate_plan("Build a REST API")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chefchat.config import LLMConfig


class LLMAdapter(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def generate(self, prompt: str, system: str | None = None) -> str:
        """Generate a response from the LLM.

        Args:
            prompt: User prompt
            system: Optional system prompt

        Returns:
            Generated response text
        """

    @abstractmethod
    async def stream(
        self, prompt: str, system: str | None = None
    ) -> AsyncIterator[str]:
        """Stream a response from the LLM.

        Args:
            prompt: User prompt
            system: Optional system prompt

        Yields:
            Response chunks
        """


class OpenAIAdapter(LLMAdapter):
    """OpenAI API adapter."""

    def __init__(self, model: str = "gpt-4", temperature: float = 0.7) -> None:
        """Initialize OpenAI adapter.

        Args:
            model: Model to use
            temperature: Response temperature
        """
        self.model = model
        self.temperature = temperature
        self._client = None

    def _get_client(self) -> Any:
        """Lazy-load OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI

                self._client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            except ImportError:
                raise ImportError(
                    "openai package required. Install with: pip install openai"
                )
        return self._client

    async def generate(self, prompt: str, system: str | None = None) -> str:
        """Generate response using OpenAI."""
        client = self._get_client()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await client.chat.completions.create(
            model=self.model, messages=messages, temperature=self.temperature
        )
        return response.choices[0].message.content or ""

    async def stream(
        self, prompt: str, system: str | None = None
    ) -> AsyncIterator[str]:
        """Stream response using OpenAI."""
        client = self._get_client()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        stream = await client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class AnthropicAdapter(LLMAdapter):
    """Anthropic Claude API adapter."""

    def __init__(
        self, model: str = "claude-3-sonnet-20240229", temperature: float = 0.7
    ) -> None:
        """Initialize Anthropic adapter.

        Args:
            model: Model to use
            temperature: Response temperature
        """
        self.model = model
        self.temperature = temperature
        self._client = None

    def _get_client(self) -> Any:
        """Lazy-load Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic

                self._client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            except ImportError:
                raise ImportError(
                    "anthropic package required. Install with: pip install anthropic"
                )
        return self._client

    async def generate(self, prompt: str, system: str | None = None) -> str:
        """Generate response using Anthropic."""
        client = self._get_client()
        response = await client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system or "You are a helpful coding assistant.",
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    async def stream(
        self, prompt: str, system: str | None = None
    ) -> AsyncIterator[str]:
        """Stream response using Anthropic."""
        client = self._get_client()
        async with client.messages.stream(
            model=self.model,
            max_tokens=4096,
            system=system or "You are a helpful coding assistant.",
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for text in stream.text_stream:
                yield text


class SimulatedAdapter(LLMAdapter):
    """Simulated adapter for when no API key is available."""

    async def generate(self, prompt: str, system: str | None = None) -> str:
        """Generate simulated response."""
        return f"""# Simulated Response

> No API key configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY.

**Your request:** {prompt[:200]}...

To enable real LLM responses, configure your `.chef/palate.toml`:
```toml
[llm]
provider = "openai"  # or "anthropic"
```

And set the appropriate environment variable.
"""

    async def stream(
        self, prompt: str, system: str | None = None
    ) -> AsyncIterator[str]:
        """Stream simulated response."""
        response = await self.generate(prompt, system)
        for char in response:
            yield char


class KitchenBrain:
    """The central intelligence of the ChefChat kitchen.

    Manages LLM interactions for planning, code generation, and reviews.
    """

    PLAN_SYSTEM_PROMPT = """You are the Sous Chef, a senior software architect.
Your job is to analyze user requests and create detailed implementation plans.

Output format:
1. Brief analysis of the request
2. Step-by-step implementation plan
3. Files to create/modify
4. Potential challenges

Be concise but thorough. Think like a Michelin-star chef planning a complex dish."""

    CODE_SYSTEM_PROMPT = """You are the Line Cook, an expert programmer.
Your job is to write clean, production-ready code based on the plan.

Rules:
- Write complete, working code
- Include docstrings and type hints
- Follow PEP 8 style
- Handle edge cases

Output only the code, no explanations unless in comments."""

    ROAST_SYSTEM_PROMPT = """You are The Critic, a sarcastic Gordon Ramsay-style code reviewer.
Your job is to roast the code while providing genuinely useful feedback.

Style:
- Be brutally honest but educational
- Use kitchen/cooking metaphors
- Point out real issues in a memorable way
- End with one genuine compliment if warranted

Keep it under 300 words. Make it sting, but make it useful."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        """Initialize the Kitchen Brain.

        Args:
            config: LLM configuration (loads from palate.toml if None)
        """
        if config is None:
            from chefchat.config import load_palate_config

            palate = load_palate_config()
            config = palate.llm

        self.config = config
        self._adapter: LLMAdapter | None = None

    def _get_adapter(self) -> LLMAdapter:
        """Get or create the appropriate LLM adapter."""
        if self._adapter is not None:
            return self._adapter

        provider = self.config.provider.lower()

        # Check for API keys first
        if provider == "openai" and os.getenv("OPENAI_API_KEY"):
            self._adapter = OpenAIAdapter(
                model=self.config.model, temperature=self.config.temperature
            )
        elif provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
            self._adapter = AnthropicAdapter(
                model=self.config.model, temperature=self.config.temperature
            )
        else:
            # Fall back to simulation
            self._adapter = SimulatedAdapter()

        return self._adapter

    async def generate_plan(self, context: str) -> str:
        """Generate an implementation plan.

        Args:
            context: User's request and any relevant context

        Returns:
            Detailed implementation plan
        """
        adapter = self._get_adapter()
        return await adapter.generate(context, system=self.PLAN_SYSTEM_PROMPT)

    async def write_code(self, plan: dict | str, context: str = "") -> str:
        """Generate code based on a plan.

        Args:
            plan: Implementation plan (dict or string)
            context: Additional context

        Returns:
            Generated code
        """
        adapter = self._get_adapter()
        if isinstance(plan, dict):
            plan_text = str(plan)
        else:
            plan_text = plan

        prompt = f"""Plan:
{plan_text}

Context:
{context}

Generate the code to implement this plan."""

        return await adapter.generate(prompt, system=self.CODE_SYSTEM_PROMPT)

    async def fix_code(self, code: str, errors: list[str]) -> str:
        """Fix code based on error messages.

        Args:
            code: The code that failed
            errors: List of error messages

        Returns:
            Fixed code
        """
        adapter = self._get_adapter()
        prompt = f"""The following code has errors:

```python
{code}
```

Errors:
{chr(10).join(f"- {e}" for e in errors)}

Fix all the errors and return the corrected code."""

        return await adapter.generate(prompt, system=self.CODE_SYSTEM_PROMPT)

    async def roast_code(self, code: str, file_path: str = "unknown") -> str:
        """Generate a sarcastic code review.

        Args:
            code: Code to review
            file_path: Optional file path for context

        Returns:
            Scathing but useful review
        """
        adapter = self._get_adapter()
        prompt = f"""Review this code from `{file_path}`:

```python
{code}
```

Give your most brutally honest, Gordon Ramsay-style code review."""

        return await adapter.generate(prompt, system=self.ROAST_SYSTEM_PROMPT)

    async def stream_response(
        self, prompt: str, system: str | None = None
    ) -> AsyncIterator[str]:
        """Stream a response for typemachine effect.

        Args:
            prompt: User prompt
            system: Optional system prompt

        Yields:
            Response chunks
        """
        adapter = self._get_adapter()
        async for chunk in adapter.stream(prompt, system):
            yield chunk
