"""
Everything that calls an AI model lives here (same as the old app):
  - chat/reasoning -> Groq, same GROQ_API_KEY and GROQ_MODEL as before
  - embeddings     -> free LOCAL model all-MiniLM-L6-v2 (384 numbers), Groq has no embeddings API
"""
import json
import logging
import time

from backend.config import GROQ_API_KEY, GROQ_MODEL, require_settings

log = logging.getLogger(__name__)

MAX_RETRIES = 5
PAUSE_BETWEEN_CALLS = 2.1  # seconds; stays under Groq's free-tier request rate

_client = None
_embedder = None
last_error = None  # the last failure swallowed by llm_json, so the UI can show a real reason


def _get_client():
    global _client
    if _client is None:
        require_settings()
        from groq import Groq
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedder


def get_embedding(text):
    """384-number numpy vector. Keep it a numpy array: the pgvector adapter only recognises arrays."""
    return get_embedder().encode(text)


def llm(prompt, json_mode=True, max_completion_tokens=2000, system=None, max_retries=MAX_RETRIES):
    """One Groq call, returns the raw reply text. Retries rate limits / overloads / bad-JSON rejections."""
    extra = {"response_format": {"type": "json_object"}} if json_mode else {}
    if "gpt-oss" in GROQ_MODEL:
        extra["reasoning_effort"] = "low"   # simple tasks: keep it fast and cheap on tokens

    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]

    for attempt in range(max_retries):
        try:
            response = _get_client().chat.completions.create(
                model=GROQ_MODEL, messages=messages, temperature=0,
                max_completion_tokens=max_completion_tokens, **extra)
            time.sleep(PAUSE_BETWEEN_CALLS)
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            message = str(e).lower()
            retryable = any(c in message for c in ("429", "503", "rate_limit", "overloaded", "json_validate_failed"))
            if retryable and attempt < max_retries - 1:
                time.sleep(2 ** attempt * 3)
                continue
            raise
    raise RuntimeError("Groq did not respond after several retries.")


def llm_json(prompt, default=None, max_completion_tokens=2000, system=None):
    """llm() in JSON mode, parsed. Returns `default` on ANY failure so one bad batch can't crash a run;
    the reason is kept in `last_error`."""
    global last_error
    try:
        raw = llm(prompt, json_mode=True, max_completion_tokens=max_completion_tokens, system=system)
        last_error = None
        return json.loads(raw)
    except Exception as e:
        last_error = f"{type(e).__name__}: {e}"
        log.warning("llm_json failed: %s", last_error)
        return default if default is not None else {}


def is_too_large(error_text):
    """True when the provider rejected the request for being bigger than its token limit (HTTP 413)."""
    t = (error_text or "").lower()
    return "413" in t or "request too large" in t or "too large" in t or "context length" in t


def friendly_error(error_text):
    """Turns a raw provider error into one sentence a user can act on. The short original is appended for support."""
    t = (error_text or "").lower()
    if "429" in t or "rate_limit" in t or "rate limit" in t:
        why = "The AI service is busy (rate limit). Please try again in a minute."
    elif "401" in t or "invalid api key" in t or "authentication" in t or "403" in t:
        why = "The AI service rejected the API key. Check GROQ_API_KEY in the .env file."
    elif is_too_large(t):
        why = "That question needed more data than the AI service accepts in one request. Try a narrower question."
    elif "timeout" in t or "connection" in t or "timed out" in t:
        why = "I couldn't reach the AI service. Check the internet connection and try again."
    else:
        why = "Sorry, I couldn't get an answer from the AI service. Please try again."
    short = (error_text or "").strip().replace("\n", " ")[:160]
    return f"{why}\n\n_Details: {short}_" if short else why
