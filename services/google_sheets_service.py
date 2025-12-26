"""
Google Sheets 自動生成サービス

ユーザーのアクセストークンを使用してGoogleスプレッドシートを作成する機能を提供。
"""

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import re


def create_spreadsheet(access_token: str, title: str, data: list[list[str]]) -> dict:
    """
    Googleスプレッドシートを作成する

    Args:
        access_token: OAuth2アクセストークン
        title: スプレッドシートのタイトル
        data: 2次元配列のデータ [[row1col1, row1col2], [row2col1, row2col2], ...]

    Returns:
        dict: {
            "spreadsheetId": str,
            "spreadsheetUrl": str
        }
    """
    try:
        creds = Credentials(token=access_token)
        service = build('sheets', 'v4', credentials=creds)

        # 1. 新しいスプレッドシートを作成
        spreadsheet = service.spreadsheets().create(
            body={
                'properties': {'title': title},
                'sheets': [{
                    'properties': {
                        'title': 'Sheet1',
                        'gridProperties': {
                            'rowCount': max(len(data) + 10, 100),
                            'columnCount': max(len(data[0]) if data else 1, 26)
                        }
                    }
                }]
            }
        ).execute()

        spreadsheet_id = spreadsheet.get('spreadsheetId')
        print(f"[Google Sheets] Created spreadsheet: {spreadsheet_id}")

        # 2. データを書き込む
        if data:
            range_name = f"Sheet1!A1:{_col_letter(len(data[0]))}{len(data)}"
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption='USER_ENTERED',
                body={'values': data}
            ).execute()
            print(f"[Google Sheets] Wrote {len(data)} rows of data")

        # 3. ヘッダー行のスタイリング（太字、背景色）
        if data:
            requests = [
                # ヘッダー行を太字に
                {
                    'repeatCell': {
                        'range': {
                            'sheetId': 0,
                            'startRowIndex': 0,
                            'endRowIndex': 1
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'textFormat': {'bold': True},
                                'backgroundColor': {
                                    'red': 0.2,
                                    'green': 0.4,
                                    'blue': 0.8
                                },
                                'horizontalAlignment': 'CENTER'
                            }
                        },
                        'fields': 'userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)'
                    }
                },
                # ヘッダーのテキスト色を白に
                {
                    'repeatCell': {
                        'range': {
                            'sheetId': 0,
                            'startRowIndex': 0,
                            'endRowIndex': 1
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'textFormat': {
                                    'bold': True,
                                    'foregroundColor': {
                                        'red': 1.0,
                                        'green': 1.0,
                                        'blue': 1.0
                                    }
                                }
                            }
                        },
                        'fields': 'userEnteredFormat.textFormat'
                    }
                },
                # 列幅を自動調整
                {
                    'autoResizeDimensions': {
                        'dimensions': {
                            'sheetId': 0,
                            'dimension': 'COLUMNS',
                            'startIndex': 0,
                            'endIndex': len(data[0]) if data else 1
                        }
                    }
                }
            ]

            try:
                service.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={'requests': requests}
                ).execute()
                print(f"[Google Sheets] Applied styling")
            except HttpError as e:
                print(f"[Google Sheets] Styling warning: {e}")

        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"

        return {
            "spreadsheetId": spreadsheet_id,
            "spreadsheetUrl": spreadsheet_url
        }

    except HttpError as error:
        print(f"[Google Sheets] API error: {error}")
        raise Exception(f"Google Sheets API error: {error.reason}")
    except Exception as error:
        print(f"[Google Sheets] Unexpected error: {error}")
        raise


def _col_letter(col_num: int) -> str:
    """列番号をアルファベットに変換 (1=A, 2=B, ..., 27=AA)"""
    result = ""
    while col_num > 0:
        col_num, remainder = divmod(col_num - 1, 26)
        result = chr(65 + remainder) + result
    return result or "A"


def parse_table_from_text(text: str) -> list[list[str]]:
    """
    テキストからテーブルデータを抽出する

    対応フォーマット:
    1. Markdown表形式 (| col1 | col2 |)
    2. タブ区切り
    3. カンマ区切り (CSV)
    4. 箇条書きリスト

    Args:
        text: AIが生成したテキスト

    Returns:
        2次元配列のデータ
    """
    if not text:
        return []

    lines = text.strip().split('\n')
    data = []

    # パターン1: Markdown表形式
    markdown_rows = []
    for line in lines:
        line = line.strip()
        if line.startswith('|') and line.endswith('|'):
            # セパレータ行（|---|---|）をスキップ
            if re.match(r'^\|[\s\-:]+\|$', line.replace('|', '|').replace('-', '-')):
                continue
            cells = [cell.strip() for cell in line.split('|')[1:-1]]
            if cells and any(cell for cell in cells):
                markdown_rows.append(cells)

    if len(markdown_rows) >= 2:
        return markdown_rows

    # パターン2: タブ区切り
    tab_rows = []
    for line in lines:
        if '\t' in line:
            cells = [cell.strip() for cell in line.split('\t')]
            if cells:
                tab_rows.append(cells)

    if len(tab_rows) >= 2:
        return tab_rows

    # パターン3: カンマ区切り (CSV形式)
    csv_rows = []
    for line in lines:
        if ',' in line and not line.startswith('#'):
            # 簡易CSVパース（クォート対応は省略）
            cells = [cell.strip().strip('"').strip("'") for cell in line.split(',')]
            if cells and len(cells) >= 2:
                csv_rows.append(cells)

    if len(csv_rows) >= 2:
        return csv_rows

    # パターン4: コロン区切りのキーバリュー形式
    kv_rows = []
    for line in lines:
        if ':' in line or '：' in line:
            # 日本語コロンも対応
            parts = re.split(r'[:：]', line, maxsplit=1)
            if len(parts) == 2:
                key = parts[0].strip().lstrip('•-*・')
                value = parts[1].strip()
                if key and value:
                    kv_rows.append([key, value])

    if len(kv_rows) >= 3:
        # ヘッダー行を追加
        return [["項目", "内容"]] + kv_rows

    # パターン5: 番号付きリストから表を生成
    numbered_items = []
    for line in lines:
        match = re.match(r'^\s*(\d+)[.）)]\s*(.+)$', line)
        if match:
            numbered_items.append([match.group(1), match.group(2).strip()])

    if len(numbered_items) >= 3:
        return [["No.", "内容"]] + numbered_items

    # パターン6: 箇条書きリスト
    bullet_items = []
    for line in lines:
        match = re.match(r'^\s*[•\-\*・]\s*(.+)$', line)
        if match:
            bullet_items.append([match.group(1).strip()])

    if len(bullet_items) >= 3:
        return [["項目"]] + bullet_items

    return []


def extract_sheet_title(task: str, ai_output: str) -> str:
    """
    タスク内容またはAI出力からシートのタイトルを抽出する
    """
    # タスクからタイトルを抽出するパターン
    title_patterns = [
        r'「(.+?)」',
        r'『(.+?)』',
        r'"(.+?)"',
        r'(.+?)の(?:スプレッドシート|シート|表|一覧|リスト)',
    ]

    for pattern in title_patterns:
        match = re.search(pattern, task)
        if match:
            title = match.group(1) if match.groups() else match.group(0)
            if title and len(title) < 50:
                return title.strip()

    # タスクの最初の部分を使用
    task_title = task[:30].strip()
    if task_title:
        return f"{task_title}..."

    return "データシート"
