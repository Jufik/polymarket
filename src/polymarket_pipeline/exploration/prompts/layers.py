"""Prompt layer models.

Three layers composed into one system prompt per agent role:
1. Platform layer (shared) — DB knowledge, schema quirks
2. Strategy layer (per strategy) — hard constraints from learnings
3. Role layer (per agent role) — reviewer, generator, explorer, brainstorm
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PromptLayer(BaseModel):
    """A single composable prompt layer."""

    name: str
    content: str
    source: Literal["static", "brainstorm", "human"] = "static"
    version: int = 1

    def render(self) -> str:
        return self.content


class PromptStack(BaseModel):
    """Ordered collection of prompt layers composed into a final prompt."""

    layers: list[PromptLayer] = Field(default_factory=list)

    def render(self) -> str:
        """Compose all layers into a single prompt string."""
        parts = [layer.render() for layer in self.layers if layer.content.strip()]
        return "\n\n".join(parts)

    def add_layer(self, layer: PromptLayer) -> None:
        self.layers.append(layer)
