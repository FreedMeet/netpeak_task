from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Category(str, Enum):
    AUTOMATION = "автоматизація"
    INTEGRATION = "інтеграція"
    REPORT_ANALYTICS = "звіт/аналітика"
    BUG_SUPPORT = "баг/підтримка"
    QUESTION_CONSULT = "питання/консультація"
    OUT_OF_SCOPE = "поза скоупом"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Department(str, Enum):
    MARKETING = "маркетинг"
    SALES = "продажі"
    HR = "HR"
    ANALYTICS = "аналітика"
    ACCOUNTING = "бухгалтерія"
    SMM = "SMM"
    IT = "IT"
    CONTENT = "контент"
    PM = "PM"
    OTHER = "інше"


class ExtractedFields(BaseModel):
    category: Category
    target_department: Optional[Department] = Field(
        default=None,
        description="Requesting department from the fixed list, null if unclear",
    )
    priority: Priority
    short_summary: str = Field(min_length=1, max_length=400)
    requested_actions: list[str] = Field(default_factory=list)
    needs_clarification: bool
    clarification_reason: Optional[str] = Field(
        default=None,
        description="Why the request is too vague, if needs_clarification=true",
    )
    possible_duplicate_of: Optional[str] = Field(
        default=None,
        description=(
            "If the request explicitly references another existing request "
            "(e.g. 'the same report Olya asked for'), put that request's id "
            "here if mentioned in the text, otherwise null."
        ),
    )

    @field_validator("requested_actions")
    @classmethod
    def _dedupe_actions(cls, v: list[str]) -> list[str]:
        seen = []
        for item in v:
            item = item.strip()
            if item and item not in seen:
                seen.append(item)
        return seen


class ProcessedRequest(BaseModel):
    id: str
    channel: str
    timestamp: str
    raw_text: str

    extracted: Optional[ExtractedFields] = None
    # Set when the LLM call fails or returns data that fails validation,
    # so the whole pipeline doesn't crash on one bad request.
    validation_error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.extracted is not None and self.validation_error is None