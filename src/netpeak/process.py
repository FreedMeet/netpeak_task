import json
import logging
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from google import genai
from pydantic import ValidationError
from netpeak import telegram_digest
from netpeak import sheets_writer

from netpeak.llm_client import (
    MODEL_NAME,
    DailyQuotaExceededError,
    LLMCallError,
    classify_request,
)
from netpeak.models import ProcessedRequest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = PROJECT_ROOT / "data" / "input_requests.csv"
OUTPUT_JSON_PATH = PROJECT_ROOT / "output" / "output.json"
REPORT_PATH = PROJECT_ROOT / "output" / "report.md"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("google_genai").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def process_all(client: genai.Client) -> list[ProcessedRequest]:
    df = pd.read_csv(DATA_PATH, dtype=str)
    total = len(df)
    results: list[ProcessedRequest] = []

    for i, (_, row) in enumerate(df.iterrows(), start=1):
        base = dict(
            id=row["id"],
            channel=row["channel"],
            timestamp=row["timestamp"],
            raw_text=row["raw_text"],
        )
        raw_text = row["raw_text"]

        if not isinstance(raw_text, str) or not raw_text.strip():
            results.append(ProcessedRequest(**base, validation_error="empty raw_text, skipped"))
            logger.warning("[%d/%d] %s skipped: empty raw_text", i, total, row["id"])
            continue

        logger.info("[%d/%d] Processing %s", i, total, row["id"])
        try:
            extracted = classify_request(client, raw_text)
            results.append(ProcessedRequest(**base, extracted=extracted))
            logger.info("[%d/%d] %s -> %s", i, total, row["id"], extracted.category.value)
        except DailyQuotaExceededError:
            logger.error(
                "[%d/%d] Daily quota exceeded for %s, stopping run (%d/%d requests done)",
                i, total, MODEL_NAME, i - 1, total,
            )
            for remaining_row in df.iloc[i - 1:].itertuples():
                results.append(
                    ProcessedRequest(
                        id=remaining_row.id,
                        channel=remaining_row.channel,
                        timestamp=remaining_row.timestamp,
                        raw_text=remaining_row.raw_text,
                        validation_error="skipped: daily quota exceeded",
                    )
                )
            break
        except (LLMCallError, ValidationError, json.JSONDecodeError) as e:
            results.append(ProcessedRequest(**base, validation_error=str(e)))
            logger.warning("[%d/%d] %s failed: %s", i, total, row["id"], type(e).__name__)

    return results


def write_output_json(results: list[ProcessedRequest]) -> None:
    OUTPUT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = [r.model_dump(mode="json") for r in results]
    OUTPUT_JSON_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def build_report(results: list[ProcessedRequest]) -> str:
    ok_results = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]

    by_category: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    by_department: dict[str, int] = {}
    needs_clarification: list[ProcessedRequest] = []

    for r in ok_results:
        e = r.extracted
        assert e is not None
        by_category[e.category.value] = by_category.get(e.category.value, 0) + 1
        by_priority[e.priority.value] = by_priority.get(e.priority.value, 0) + 1
        dept = e.target_department.value if e.target_department else "не визначено"
        by_department[dept] = by_department.get(dept, 0) + 1
        if e.needs_clarification:
            needs_clarification.append(r)

    lines = ["# Request Classification Report", ""]
    lines.append(f"Total requests: {len(results)} (processed: {len(ok_results)}, failed: {len(failed)})")
    lines.append("")

    lines.append("## By category")
    for cat, count in sorted(by_category.items(), key=lambda x: -x[1]):
        lines.append(f"- {cat}: {count}")
    lines.append("")

    lines.append("## By priority")
    for pr, count in sorted(by_priority.items(), key=lambda x: -x[1]):
        lines.append(f"- {pr}: {count}")
    lines.append("")

    lines.append("## By department")
    for dept, count in sorted(by_department.items(), key=lambda x: -x[1]):
        lines.append(f"- {dept}: {count}")
    lines.append("")

    lines.append(f"## Needs clarification ({len(needs_clarification)})")
    for r in needs_clarification:
        assert r.extracted is not None
        lines.append(f"- **{r.id}**: {r.extracted.short_summary}")
        if r.extracted.clarification_reason:
            lines.append(f"  - reason: {r.extracted.clarification_reason}")
    lines.append("")

    if failed:
        lines.append(f"## Failed to process ({len(failed)})")
        for r in failed:
            lines.append(f"- **{r.id}**: {r.validation_error}")

    return "\n".join(lines)


def write_report(results: list[ProcessedRequest]) -> None:
    REPORT_PATH.write_text(build_report(results), encoding="utf-8")


def main() -> None:
    load_dotenv()
    client = genai.Client()
    results = process_all(client)
    write_output_json(results)
    write_report(results)
    sheets_writer.write_results(results)
    print(f"Processed {len(results)} requests -> {OUTPUT_JSON_PATH}, {REPORT_PATH}")

    ok_count = sum(1 for r in results if r.ok)
    summary = f"Netpeak request classification: {ok_count}/{len(results)} processed successfully"
    telegram_digest.send_report(REPORT_PATH, summary)


if __name__ == "__main__":
    main()