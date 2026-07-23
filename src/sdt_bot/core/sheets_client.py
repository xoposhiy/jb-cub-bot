from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def fetch_rows(sheet_id: str, credentials_file: str, range_: str = "A:Z") -> list[list[str]]:
    creds = Credentials.from_service_account_file(credentials_file, scopes=_SCOPES)
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=range_)
        .execute()
    )
    return result.get("values", [])
