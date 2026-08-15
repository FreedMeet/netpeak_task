import json
import logging
import re
import os

from dotenv import load_dotenv

from google import genai
from google.genai import types
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from netpeak.models import ExtractedFields

load_dotenv()
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

SYSTEM_PROMPT = """\
You are a triage assistant for an internal AI team at a company. \
You receive free-form requests from internal teams (marketing, sales, \
analytics, PM, HR) sent via Slack, Telegram or email, in Ukrainian \
(sometimes mixed with English).

Extract structured fields from each request according to the given schema.

Guidance:
- A short message with no concrete ask (e.g. "guys we need a bot") is too \
vague to act on: set needs_clarification=true and explain why in \
clarification_reason.
- A message that is just gratitude or an off-topic request unrelated to \
AI/automation (e.g. buying hardware) belongs to category "поза скоупом".
- If a request explicitly refers to another request already made by someone \
else, note that in possible_duplicate_of.
- priority should reflect urgency language and business impact, not just \
tone (e.g. "ГОРИТЬ", explicit deadlines -> high).
- requested_actions should be concrete, verb-first actions, not a restatement \
of the whole message.
- target_department must be one of the enum values, or null if genuinely \
unclear — do not invent a department name outside the given list.
"""

RETRY_DELAY_PATTERN = re.compile(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'")
logger = logging.getLogger(__name__)


class LLMCallError(Exception):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class DailyQuotaExceededError(Exception):
    pass


def _extract_retry_delay(exc: Exception) -> float | None:
    match = RETRY_DELAY_PATTERN.search(str(exc))
    return float(match.group(1)) + 1 if match else None


def _wait_strategy(retry_state) -> float:
    exc = retry_state.outcome.exception()
    if isinstance(exc, LLMCallError) and exc.retry_after is not None:
        return exc.retry_after
    return min(2 * 2**retry_state.attempt_number, 30)


def _log_retry(retry_state) -> None:
    exc = retry_state.outcome.exception()
    wait = retry_state.next_action.sleep if retry_state.next_action else 0
    reason = "rate limit" if isinstance(exc, LLMCallError) and exc.retry_after else "call error"
    logger.warning("Retry #%d (%s), waiting %.0fs", retry_state.attempt_number, reason, wait)


@retry(
    retry=retry_if_exception_type(LLMCallError),
    stop=stop_after_attempt(5),
    wait=_wait_strategy,
    reraise=True,
    before_sleep=_log_retry,
)
def _call_gemini(client: genai.Client, contents: str) -> str:
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=ExtractedFields,
            ),
        )
    except Exception as e:
        if "PerDay" in str(e):
            raise DailyQuotaExceededError(str(e)) from e
        raise LLMCallError(str(e), retry_after=_extract_retry_delay(e)) from e

    text = response.text
    if not text:
        raise LLMCallError("empty response from model")

    return text


def classify_request(client: genai.Client, raw_text: str) -> ExtractedFields:
    raw_json = _call_gemini(client, raw_text)
    try:
        return ExtractedFields.model_validate(json.loads(raw_json))
    except (ValidationError, json.JSONDecodeError) as e:
        logger.warning("Invalid structured output, retrying once with repair prompt")
        repair_prompt = (
            f"Original request: {raw_text}\n\n"
            f"Your previous answer failed validation with this error:\n{e}\n\n"
            "Return a corrected JSON object matching the schema."
        )
        raw_json = _call_gemini(client, repair_prompt)
        return ExtractedFields.model_validate(json.loads(raw_json))