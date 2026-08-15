import logging
import os

import gspread
from google.oauth2.service_account import Credentials

from netpeak.models import ProcessedRequest

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = [
    "id",
    "channel",
    "timestamp",
    "category",
    "target_department",
    "priority",
    "short_summary",
    "requested_actions",
    "needs_clarification",
    "clarification_reason",
    "possible_duplicate_of",
    "validation_error",
]


def _row_for(r: ProcessedRequest) -> list[str]:
    if r.extracted is None:
        return [
            r.id, r.channel, r.timestamp,
            "", "", "", "", "", "", "", "",
            r.validation_error or "",
        ]
    e = r.extracted
    return [
        r.id, r.channel, r.timestamp,
        e.category.value,
        e.target_department.value if e.target_department else "",
        e.priority.value,
        e.short_summary,
        "; ".join(e.requested_actions),
        str(e.needs_clarification),
        e.clarification_reason or "",
        e.possible_duplicate_of or "",
        "",
    ]


def write_results(results: list[ProcessedRequest]) -> None:
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")

    if not spreadsheet_id or not service_account_file:
        logger.info("Google Sheets not configured, skipping")
        return

    try:
        creds = Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(spreadsheet_id).sheet1

        rows = [HEADER] + [_row_for(r) for r in results]
        sheet.clear()
        sheet.update(values=rows, range_name="A1")
        logger.info("Results written to Google Sheets (%d rows)", len(results))
    except Exception as e:
        logger.warning("Failed to write to Google Sheets: %s", e)