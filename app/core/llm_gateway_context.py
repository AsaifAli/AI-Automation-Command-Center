
from contextvars import ContextVar, Token

_llm_gateway_token: ContextVar[str] = ContextVar(
    "llm_gateway_token",
    default="",
)


def set_llm_gateway_token(token: str) -> Token[str]:
    return _llm_gateway_token.set(
        (token or "").strip()
    )


def reset_llm_gateway_token(token: Token[str]) -> None:
    _llm_gateway_token.reset(token)


def get_llm_gateway_token() -> str:
    return _llm_gateway_token.get()
