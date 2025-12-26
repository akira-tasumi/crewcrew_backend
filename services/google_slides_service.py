"""
Google Slides 自動生成サービス

ユーザーのアクセストークンを使用してGoogleスライドを作成する機能を提供。
デザイン強化版：フォントサイズ、色、スタイリングを適用。
"""

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import uuid
import re


# カラーパレット（モダンなビジネス向け）
COLORS = {
    'primary': {'red': 0.2, 'green': 0.4, 'blue': 0.8},      # 青
    'secondary': {'red': 0.1, 'green': 0.6, 'blue': 0.5},    # ティール
    'accent': {'red': 0.9, 'green': 0.3, 'blue': 0.2},       # オレンジレッド
    'dark': {'red': 0.2, 'green': 0.2, 'blue': 0.25},        # ダークグレー
    'light': {'red': 0.95, 'green': 0.95, 'blue': 0.97},     # ライトグレー
    'white': {'red': 1.0, 'green': 1.0, 'blue': 1.0},
}


def _parse_slide_content(page_text: str) -> dict:
    """
    スライドのテキストからタイトルと本文を分離する

    期待する形式:
    - 1行目: タイトル（または「スライドN: タイトル」形式）
    - 2行目以降: 本文（箇条書きなど）

    Returns:
        {"title": str, "body": str, "has_emoji": bool}
    """
    lines = page_text.strip().split('\n')
    if not lines:
        return {"title": "スライド", "body": "", "has_emoji": False}

    # 1行目からタイトルを抽出
    first_line = lines[0].strip()

    # 「スライドN:」プレフィックスを除去
    title_match = re.match(r'^(?:スライド|Slide)\s*\d+\s*[:：]\s*(.+)$', first_line, re.IGNORECASE)
    if title_match:
        title = title_match.group(1).strip()
    else:
        title = first_line

    # タイトルから絵文字行を検出（📌💡🎯など）
    has_emoji = bool(re.search(r'[\U0001F300-\U0001F9FF]', title))

    # 本文を構築（2行目以降）
    body_lines = lines[1:] if len(lines) > 1 else []

    # 本文の最初の行が絵文字+テキストの場合、それをサブタイトルとして扱う
    subtitle = ""
    if body_lines:
        first_body = body_lines[0].strip()
        if re.match(r'^[\U0001F300-\U0001F9FF]', first_body):
            subtitle = first_body
            body_lines = body_lines[1:]

    body = '\n'.join(line for line in body_lines if line.strip())

    # サブタイトルがあれば本文の先頭に追加
    if subtitle:
        body = f"{subtitle}\n\n{body}" if body else subtitle

    return {
        "title": title,
        "body": body,
        "has_emoji": has_emoji
    }


def _create_text_style_request(object_id: str, start: int, end: int,
                                font_size: int = None, bold: bool = False,
                                color: dict = None) -> dict:
    """テキストスタイル更新リクエストを生成"""
    style = {}
    fields = []

    if font_size:
        style['fontSize'] = {'magnitude': font_size, 'unit': 'PT'}
        fields.append('fontSize')

    if bold:
        style['bold'] = True
        fields.append('bold')

    if color:
        style['foregroundColor'] = {'opaqueColor': {'rgbColor': color}}
        fields.append('foregroundColor')

    if not fields:
        return None

    return {
        'updateTextStyle': {
            'objectId': object_id,
            'textRange': {'type': 'FIXED_RANGE', 'startIndex': start, 'endIndex': end},
            'style': style,
            'fields': ','.join(fields)
        }
    }


def create_presentation(access_token: str, title: str, pages: list[str]) -> dict:
    """
    Googleスライドを作成する（デザイン強化版）

    Args:
        access_token: OAuth2アクセストークン
        title: プレゼンテーションのタイトル
        pages: 各スライドの本文テキストのリスト

    Returns:
        dict: {
            "presentationId": str,
            "presentationUrl": str
        }
    """
    try:
        creds = Credentials(token=access_token)
        service = build('slides', 'v1', credentials=creds)

        # 1. 新しいプレゼンテーションを作成
        presentation = service.presentations().create(
            body={'title': title}
        ).execute()

        presentation_id = presentation.get('presentationId')
        print(f"[Google Slides] Created presentation: {presentation_id}")

        slides = presentation.get('slides', [])
        first_slide_id = slides[0].get('objectId') if slides else None

        # リクエストを2段階で構築（作成→スタイリング）
        create_requests = []
        style_requests = []

        # スライド作成用のデータを保持
        slide_data = []

        for i, page_text in enumerate(pages):
            parsed = _parse_slide_content(page_text)

            slide_id = f"slide_{uuid.uuid4().hex[:8]}"
            title_id = f"title_{uuid.uuid4().hex[:8]}"
            body_id = f"body_{uuid.uuid4().hex[:8]}"

            slide_data.append({
                'slide_id': slide_id,
                'title_id': title_id,
                'body_id': body_id,
                'title': parsed['title'],
                'body': parsed['body'],
                'index': i
            })

            # スライドを追加
            create_requests.append({
                'createSlide': {
                    'objectId': slide_id,
                    'insertionIndex': i + 1,
                    'slideLayoutReference': {
                        'predefinedLayout': 'TITLE_AND_BODY'
                    },
                    'placeholderIdMappings': [
                        {
                            'layoutPlaceholder': {'type': 'TITLE', 'index': 0},
                            'objectId': title_id
                        },
                        {
                            'layoutPlaceholder': {'type': 'BODY', 'index': 0},
                            'objectId': body_id
                        }
                    ]
                }
            })

            # タイトルを挿入
            create_requests.append({
                'insertText': {
                    'objectId': title_id,
                    'insertionIndex': 0,
                    'text': parsed['title']
                }
            })

            # 本文を挿入
            if parsed['body']:
                create_requests.append({
                    'insertText': {
                        'objectId': body_id,
                        'insertionIndex': 0,
                        'text': parsed['body']
                    }
                })

        # タイトルスライドの設定
        title_slide_title_id = None
        if slides:
            title_slide = slides[0]
            for element in title_slide.get('pageElements', []):
                shape = element.get('shape', {})
                placeholder = shape.get('placeholder', {})
                placeholder_type = placeholder.get('type', '')

                if placeholder_type in ['CENTERED_TITLE', 'TITLE']:
                    title_slide_title_id = element.get('objectId')
                    create_requests.insert(0, {
                        'insertText': {
                            'objectId': title_slide_title_id,
                            'insertionIndex': 0,
                            'text': title
                        }
                    })
                    break

        # 2. まずスライド作成とテキスト挿入を実行
        if create_requests:
            service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={'requests': create_requests}
            ).execute()
            print(f"[Google Slides] Created {len(pages)} slides with content")

        # 3. スタイリングを適用
        for data in slide_data:
            title_len = len(data['title'])
            body_text = data['body']

            # タイトルのスタイリング（太字、プライマリカラー）
            if title_len > 0:
                style_req = _create_text_style_request(
                    data['title_id'], 0, title_len,
                    font_size=28, bold=True, color=COLORS['primary']
                )
                if style_req:
                    style_requests.append(style_req)

            # 本文のスタイリング
            if body_text:
                body_len = len(body_text)

                # 本文全体の基本スタイル
                style_req = _create_text_style_request(
                    data['body_id'], 0, body_len,
                    font_size=16, color=COLORS['dark']
                )
                if style_req:
                    style_requests.append(style_req)

                # 絵文字行（サブタイトル）を強調
                lines = body_text.split('\n')
                pos = 0
                for line in lines:
                    line_len = len(line)
                    # 絵文字で始まる行を強調
                    if re.match(r'^[\U0001F300-\U0001F9FF]', line):
                        style_req = _create_text_style_request(
                            data['body_id'], pos, pos + line_len,
                            font_size=20, bold=True, color=COLORS['secondary']
                        )
                        if style_req:
                            style_requests.append(style_req)
                    pos += line_len + 1  # +1 for newline

        # タイトルスライドのスタイリング
        if title_slide_title_id and title:
            style_req = _create_text_style_request(
                title_slide_title_id, 0, len(title),
                font_size=44, bold=True, color=COLORS['primary']
            )
            if style_req:
                style_requests.append(style_req)

        # スタイリングリクエストを実行
        if style_requests:
            try:
                service.presentations().batchUpdate(
                    presentationId=presentation_id,
                    body={'requests': style_requests}
                ).execute()
                print(f"[Google Slides] Applied styling to slides")
            except HttpError as e:
                # スタイリングエラーは無視（スライドは作成済み）
                print(f"[Google Slides] Styling warning: {e}")

        presentation_url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"

        return {
            "presentationId": presentation_id,
            "presentationUrl": presentation_url
        }

    except HttpError as error:
        print(f"[Google Slides] API error: {error}")
        raise Exception(f"Google Slides API error: {error.reason}")
    except Exception as error:
        print(f"[Google Slides] Unexpected error: {error}")
        raise


def create_presentation_from_summary(
    access_token: str,
    title: str,
    summary_sections: list[dict]
) -> dict:
    """
    要約セクションからスライドを作成する（デザイン強化版）

    Args:
        access_token: OAuth2アクセストークン
        title: プレゼンテーションのタイトル
        summary_sections: [{"heading": str, "content": str}, ...]

    Returns:
        dict: {
            "presentationId": str,
            "presentationUrl": str
        }
    """
    try:
        creds = Credentials(token=access_token)
        service = build('slides', 'v1', credentials=creds)

        presentation = service.presentations().create(
            body={'title': title}
        ).execute()

        presentation_id = presentation.get('presentationId')
        slides = presentation.get('slides', [])

        create_requests = []
        style_requests = []
        slide_data = []

        # タイトルスライドを更新
        title_slide_title_id = None
        if slides:
            first_slide = slides[0]
            for element in first_slide.get('pageElements', []):
                shape = element.get('shape', {})
                placeholder = shape.get('placeholder', {})
                placeholder_type = placeholder.get('type', '')

                if placeholder_type in ['CENTERED_TITLE', 'TITLE']:
                    title_slide_title_id = element.get('objectId')
                    create_requests.append({
                        'insertText': {
                            'objectId': title_slide_title_id,
                            'insertionIndex': 0,
                            'text': title
                        }
                    })
                    break

        # 各セクションのスライドを追加
        for i, section in enumerate(summary_sections):
            slide_id = f"slide_{uuid.uuid4().hex[:8]}"
            title_id = f"title_{uuid.uuid4().hex[:8]}"
            body_id = f"body_{uuid.uuid4().hex[:8]}"

            heading = section.get('heading', f'セクション {i + 1}')
            content = section.get('content', '')

            slide_data.append({
                'title_id': title_id,
                'body_id': body_id,
                'heading': heading,
                'content': content
            })

            create_requests.append({
                'createSlide': {
                    'objectId': slide_id,
                    'insertionIndex': i + 1,
                    'slideLayoutReference': {
                        'predefinedLayout': 'TITLE_AND_BODY'
                    },
                    'placeholderIdMappings': [
                        {
                            'layoutPlaceholder': {'type': 'TITLE', 'index': 0},
                            'objectId': title_id
                        },
                        {
                            'layoutPlaceholder': {'type': 'BODY', 'index': 0},
                            'objectId': body_id
                        }
                    ]
                }
            })

            create_requests.append({
                'insertText': {
                    'objectId': title_id,
                    'insertionIndex': 0,
                    'text': heading
                }
            })

            if content:
                create_requests.append({
                    'insertText': {
                        'objectId': body_id,
                        'insertionIndex': 0,
                        'text': content
                    }
                })

        # スライド作成を実行
        if create_requests:
            service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={'requests': create_requests}
            ).execute()

        # スタイリングを適用
        for data in slide_data:
            heading_len = len(data['heading'])
            content = data['content']

            if heading_len > 0:
                style_req = _create_text_style_request(
                    data['title_id'], 0, heading_len,
                    font_size=28, bold=True, color=COLORS['primary']
                )
                if style_req:
                    style_requests.append(style_req)

            if content:
                style_req = _create_text_style_request(
                    data['body_id'], 0, len(content),
                    font_size=16, color=COLORS['dark']
                )
                if style_req:
                    style_requests.append(style_req)

        # タイトルスライドのスタイリング
        if title_slide_title_id and title:
            style_req = _create_text_style_request(
                title_slide_title_id, 0, len(title),
                font_size=44, bold=True, color=COLORS['primary']
            )
            if style_req:
                style_requests.append(style_req)

        if style_requests:
            try:
                service.presentations().batchUpdate(
                    presentationId=presentation_id,
                    body={'requests': style_requests}
                ).execute()
            except HttpError:
                pass  # スタイリングエラーは無視

        return {
            "presentationId": presentation_id,
            "presentationUrl": f"https://docs.google.com/presentation/d/{presentation_id}/edit"
        }

    except HttpError as error:
        raise Exception(f"Google Slides API error: {error.reason}")
    except Exception as error:
        raise
