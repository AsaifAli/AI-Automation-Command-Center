from abc import ABC, abstractmethod
from typing import Any

from app.integrations.llm import LLMProvider


class Agent(ABC):
    name: str

    def __init__(self, llm: LLMProvider):
        self.llm = llm

    @abstractmethod
    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError
