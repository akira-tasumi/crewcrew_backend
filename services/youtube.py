"""
YouTube字幕取得サービス

YouTubeの動画URLから字幕（Transcript）を取得する機能を提供。
"""

import re
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound


def extract_video_id(url: str) -> str | None:
    """
    YouTubeのURLから動画IDを抽出する

    対応形式:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://www.youtube.com/embed/VIDEO_ID
    - https://www.youtube.com/v/VIDEO_ID

    Args:
        url: YouTubeのURL

    Returns:
        動画ID（抽出できない場合はNone）
    """
    if not url:
        return None

    # 各種URLパターンに対応するRegex
    patterns = [
        # 標準的なwatch URL (v=...)
        r'(?:youtube\.com/watch\?.*v=|youtube\.com/watch\?v=)([a-zA-Z0-9_-]{11})',
        # 短縮URL (youtu.be/...)
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        # 埋め込みURL (embed/...)
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        # 旧形式URL (v/...)
        r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return None


def get_video_transcript(video_id: str) -> str | None:
    """
    動画IDから字幕テキストを取得する

    優先順位:
    1. 日本語字幕 (ja)
    2. 英語字幕 (en)
    3. 利用可能な最初の字幕

    Args:
        video_id: YouTube動画ID

    Returns:
        字幕テキスト（取得できない場合はNone）
    """
    try:
        print(f"[YouTube] Fetching transcript for video_id: {video_id}")

        # 日本語優先、次に英語、それ以外は自動
        languages_to_try = ['ja', 'en']

        transcript_data = None
        used_language = None

        for lang in languages_to_try:
            try:
                transcript_data = YouTubeTranscriptApi.get_transcript(video_id, languages=[lang])
                used_language = lang
                print(f"[YouTube] Found {lang} transcript")
                break
            except Exception:
                continue

        # 指定言語で見つからない場合は、利用可能な字幕を取得
        if not transcript_data:
            try:
                transcript_data = YouTubeTranscriptApi.get_transcript(video_id)
                used_language = "auto"
                print(f"[YouTube] Using auto-detected transcript")
            except Exception as e:
                print(f"[YouTube] Could not fetch any transcript: {e}")
                return None

        if not transcript_data:
            print(f"[YouTube] No transcript available for video_id: {video_id}")
            return None

        # 字幕データを連結
        full_text = " ".join([snippet['text'] for snippet in transcript_data])

        # トークン制限対策: 長すぎる場合は先頭5000文字でカット
        if len(full_text) > 5000:
            print(f"[YouTube] Transcript truncated from {len(full_text)} to 5000 chars")
            full_text = full_text[:5000]

        print(f"[YouTube] Successfully fetched transcript ({len(full_text)} chars, lang: {used_language})")
        return full_text

    except TranscriptsDisabled:
        print(f"[YouTube] Transcripts are disabled for video_id: {video_id}")
        return None
    except NoTranscriptFound:
        print(f"[YouTube] No transcript found for video_id: {video_id}")
        return None
    except Exception as e:
        print(f"[YouTube] Transcript Error: {e}")
        return None


def get_transcript_from_url(url: str) -> tuple[str | None, str]:
    """
    URLから字幕を取得する便利関数

    Args:
        url: YouTubeのURL

    Returns:
        tuple: (字幕テキスト or None, ステータスメッセージ)
    """
    video_id = extract_video_id(url)

    if not video_id:
        return None, "Invalid YouTube URL - could not extract video ID"

    transcript = get_video_transcript(video_id)

    if transcript:
        return transcript, f"Successfully fetched transcript for video {video_id}"
    else:
        return None, f"Could not fetch transcript for video {video_id}"
