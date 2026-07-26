import json

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def build_credentials(credentials_file: str, credentials_json: str) -> Credentials:
    """Service-account credentials from an inline JSON blob or a key file.

    Inline JSON wins when present: hosts like Railway can only pass secrets as
    environment variables, while local development keeps using the file.
    """
    if not credentials_json and not credentials_file:
        raise ValueError(
            "No Google service-account credentials configured: set either "
            "GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE."
        )
    if credentials_json:
        return Credentials.from_service_account_info(
            json.loads(credentials_json), scopes=_SCOPES
        )
    return Credentials.from_service_account_file(credentials_file, scopes=_SCOPES)


def fetch_rows(
    sheet_id: str, credentials: Credentials, range_: str = "A:Z"
) -> list[list[str]]:
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=range_)
        .execute()
    )
    return result.get("values", [])
