"""
Google Sheets読込サービス

公開されたGoogle SheetsからCSVデータを取得する
"""

import re
import logging
import requests

logger = logging.getLogger(__name__)


def extract_sheet_id(url: str) -> str | None:
    """
    Google SheetsのURLからSheet IDを抽出する

    Args:
        url: Google SheetsのURL

    Returns:
        str | None: Sheet ID、抽出できない場合はNone
    """
    # パターン1: /spreadsheets/d/{id}/...
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)

    # パターン2: ?id={id} または &id={id}
    match = re.search(r'[?&]id=([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)

    return None


def is_google_sheets_url(url: str) -> bool:
    """
    URLがGoogle SheetsのURLかどうかを判定する

    Args:
        url: 判定するURL

    Returns:
        bool: Google SheetsのURLならTrue
    """
    return 'docs.google.com/spreadsheets' in url


def read_public_sheet(url: str, sheet_name: str = None) -> str:
    """
    公開されたGoogle SheetsからCSVデータを取得する

    Args:
        url: Google SheetsのURL
        sheet_name: 特定のシート名（オプション）

    Returns:
        str: CSVテキストデータ

    Raises:
        ValueError: URLが無効な場合
        requests.RequestException: リクエストエラーの場合
    """
    sheet_id = extract_sheet_id(url)

    if not sheet_id:
        raise ValueError(f"Invalid Google Sheets URL: {url}")

    # CSV形式でエクスポートするURL
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"

    if sheet_name:
        # 特定のシートを指定
        export_url += f"&gid={sheet_name}"

    logger.info(f"Fetching Google Sheet: {sheet_id}")

    try:
        response = requests.get(
            export_url,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )

        if response.status_code == 200:
            csv_text = response.text

            # CSVが空でないことを確認
            if not csv_text.strip():
                raise ValueError("The spreadsheet appears to be empty")

            logger.info(f"Successfully fetched Google Sheet: {len(csv_text)} characters")
            return csv_text

        elif response.status_code == 404:
            raise ValueError("Spreadsheet not found. Make sure it exists and is publicly accessible.")

        elif response.status_code == 403:
            raise ValueError("Access denied. Make sure the spreadsheet is set to 'Anyone with the link can view'.")

        else:
            raise ValueError(f"Failed to fetch spreadsheet: HTTP {response.status_code}")

    except requests.exceptions.Timeout:
        raise ValueError("Request timed out. Please try again.")

    except requests.exceptions.RequestException as e:
        raise ValueError(f"Network error: {str(e)}")


def format_csv_for_prompt(csv_text: str, max_rows: int = 100) -> str:
    """
    CSVテキストをAIプロンプト用に整形する

    Args:
        csv_text: CSVテキスト
        max_rows: 最大行数（デフォルト100行）

    Returns:
        str: 整形されたテキスト
    """
    lines = csv_text.strip().split('\n')

    if len(lines) > max_rows:
        # 最初の行（ヘッダー）と最初のmax_rows-1行のデータを保持
        truncated_lines = lines[:max_rows]
        truncated_lines.append(f"... (残り {len(lines) - max_rows} 行は省略)")
        return '\n'.join(truncated_lines)

    return csv_text
