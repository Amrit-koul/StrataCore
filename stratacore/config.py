import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@lru_cache
def get_database_path() -> str:
    return os.environ.get("STRATACORE_DB", "store.db")


def llm_available() -> bool:
    return bool(os.environ.get("GROQ_API_KEY", ""))
