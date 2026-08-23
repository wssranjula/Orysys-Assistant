"""Shared LangSmith client construction."""

from functools import lru_cache

from langsmith import Client


@lru_cache(maxsize=4)
def get_langsmith_client(api_key: str, api_url: str) -> Client:
    """Return a shared client configured independently of process environment variables."""

    return Client(api_key=api_key, api_url=api_url)
