"""
YouTube字幕取得サービス

YouTubeの動画URLから字幕（Transcript）を取得する機能を提供。
複数の方法でフォールバック:
1. youtube-transcript-api (新API)
2. youtube-transcript-api (旧API)
3. yt-dlp (より堅牢、AWS環境での制限回避)
"""

import re
import subprocess
import json
import os
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


def _fetch_with_new_api(video_id: str, languages: list[str] | None) -> list[dict] | None:
    """新しいAPI (インスタンスメソッド) を試す"""
    try:
        ytt_api = YouTubeTranscriptApi()
        if languages:
            result = ytt_api.fetch(video_id, languages=languages)
        else:
            result = ytt_api.fetch(video_id)
        # FetchedTranscriptをリストに変換
        return [{'text': item.text} for item in result]
    except AttributeError:
        # 新しいAPIがない場合
        return None
    except Exception as e:
        raise e


def _fetch_with_old_api(video_id: str, languages: list[str] | None) -> list[dict] | None:
    """旧API (クラスメソッド) を試す"""
    try:
        if languages:
            return YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
        else:
            return YouTubeTranscriptApi.get_transcript(video_id)
    except AttributeError:
        # 旧APIがない場合
        return None
    except Exception as e:
        raise e


def _fetch_with_ytdlp(video_id: str) -> list[dict] | None:
    """
    yt-dlpを使って字幕を取得する（AWS環境でのフォールバック）
    yt-dlpはより堅牢でプロキシサポートもある
    """
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"

        # yt-dlpで字幕情報を取得
        cmd = [
            "yt-dlp",
            "--skip-download",
            "--write-auto-sub",
            "--sub-lang", "ja,en",
            "--sub-format", "json3",
            "--print", "%(subtitles)j",
            "--no-warnings",
            url
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            print(f"[YouTube] yt-dlp failed: {result.stderr}")
            return None

        # 字幕JSONをパース
        try:
            subtitles_info = json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError:
            print(f"[YouTube] yt-dlp: Could not parse subtitles JSON")
            return None

        if not subtitles_info:
            # 自動生成字幕を試す
            cmd_auto = [
                "yt-dlp",
                "--skip-download",
                "--write-auto-sub",
                "--sub-lang", "ja,en",
                "--print", "%(automatic_captions)j",
                "--no-warnings",
                url
            ]

            result_auto = subprocess.run(
                cmd_auto,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result_auto.returncode == 0 and result_auto.stdout.strip():
                try:
                    subtitles_info = json.loads(result_auto.stdout)
                except json.JSONDecodeError:
                    pass

        if not subtitles_info:
            print(f"[YouTube] yt-dlp: No subtitles found")
            return None

        # 字幕を実際にダウンロード
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            output_template = os.path.join(tmpdir, "%(id)s")

            download_cmd = [
                "yt-dlp",
                "--skip-download",
                "--write-sub",
                "--write-auto-sub",
                "--sub-lang", "ja,en",
                "--sub-format", "vtt",
                "--convert-subs", "srt",
                "-o", output_template,
                "--no-warnings",
                url
            ]

            subprocess.run(download_cmd, capture_output=True, timeout=30)

            # ダウンロードされた字幕ファイルを探す
            for lang in ["ja", "en"]:
                srt_path = os.path.join(tmpdir, f"{video_id}.{lang}.srt")
                if os.path.exists(srt_path):
                    with open(srt_path, "r", encoding="utf-8") as f:
                        srt_content = f.read()
                    # SRTからテキストを抽出
                    text = _parse_srt(srt_content)
                    if text:
                        print(f"[YouTube] yt-dlp: Found {lang} subtitles")
                        return [{"text": text}]

        return None

    except subprocess.TimeoutExpired:
        print(f"[YouTube] yt-dlp: Timeout")
        return None
    except FileNotFoundError:
        print(f"[YouTube] yt-dlp: Not installed")
        return None
    except Exception as e:
        print(f"[YouTube] yt-dlp error: {e}")
        return None


def _parse_srt(srt_content: str) -> str:
    """SRTファイルからテキスト部分のみを抽出"""
    lines = srt_content.split('\n')
    text_lines = []

    for line in lines:
        line = line.strip()
        # 番号行やタイムスタンプ行をスキップ
        if not line:
            continue
        if line.isdigit():
            continue
        if '-->' in line:
            continue
        # HTMLタグを除去
        line = re.sub(r'<[^>]+>', '', line)
        if line:
            text_lines.append(line)

    return ' '.join(text_lines)


def get_video_transcript(video_id: str) -> str | None:
    """
    動画IDから字幕テキストを取得する

    優先順位:
    1. youtube-transcript-api (日本語 → 英語 → 自動)
    2. yt-dlp フォールバック (AWS環境での制限回避用)

    Args:
        video_id: YouTube動画ID

    Returns:
        字幕テキスト（取得できない場合はNone）
    """
    try:
        print(f"[YouTube] Fetching transcript for video_id: {video_id}")

        # 日本語優先、次に英語、それ以外は自動
        languages_to_try = [['ja'], ['en'], None]

        transcript_data = None
        used_language = None
        api_failed = False

        for lang in languages_to_try:
            try:
                # 新しいAPIを試す
                transcript_data = _fetch_with_new_api(video_id, lang)
                if transcript_data is None:
                    # 旧APIを試す
                    transcript_data = _fetch_with_old_api(video_id, lang)

                if transcript_data:
                    used_language = lang[0] if lang else "auto"
                    print(f"[YouTube] Found {used_language} transcript via API")
                    break
            except (TranscriptsDisabled, NoTranscriptFound) as e:
                print(f"[YouTube] API failed with lang={lang}: {type(e).__name__}")
                api_failed = True
                continue
            except Exception as e:
                print(f"[YouTube] Failed to fetch with lang={lang}: {type(e).__name__}: {e}")
                api_failed = True
                continue

        # youtube-transcript-api が失敗した場合、yt-dlp を試す
        if not transcript_data and api_failed:
            print(f"[YouTube] Trying yt-dlp fallback...")
            transcript_data = _fetch_with_ytdlp(video_id)
            if transcript_data:
                used_language = "yt-dlp"

        if not transcript_data:
            print(f"[YouTube] No transcript available for video_id: {video_id}")
            return None

        # 字幕データを連結（辞書のリスト形式）
        full_text = " ".join([snippet['text'] for snippet in transcript_data])

        # トークン制限対策: 長すぎる場合は先頭5000文字でカット
        if len(full_text) > 5000:
            print(f"[YouTube] Transcript truncated from {len(full_text)} to 5000 chars")
            full_text = full_text[:5000]

        print(f"[YouTube] Successfully fetched transcript ({len(full_text)} chars, method: {used_language})")
        return full_text

    except TranscriptsDisabled:
        print(f"[YouTube] Transcripts are disabled for video_id: {video_id}")
        # yt-dlp フォールバック
        print(f"[YouTube] Trying yt-dlp fallback...")
        transcript_data = _fetch_with_ytdlp(video_id)
        if transcript_data:
            full_text = " ".join([snippet['text'] for snippet in transcript_data])
            if len(full_text) > 5000:
                full_text = full_text[:5000]
            return full_text
        return None
    except NoTranscriptFound:
        print(f"[YouTube] No transcript found for video_id: {video_id}")
        # yt-dlp フォールバック
        print(f"[YouTube] Trying yt-dlp fallback...")
        transcript_data = _fetch_with_ytdlp(video_id)
        if transcript_data:
            full_text = " ".join([snippet['text'] for snippet in transcript_data])
            if len(full_text) > 5000:
                full_text = full_text[:5000]
            return full_text
        return None
    except Exception as e:
        print(f"[YouTube] Transcript Error: {e}")
        # yt-dlp フォールバック
        print(f"[YouTube] Trying yt-dlp fallback...")
        transcript_data = _fetch_with_ytdlp(video_id)
        if transcript_data:
            full_text = " ".join([snippet['text'] for snippet in transcript_data])
            if len(full_text) > 5000:
                full_text = full_text[:5000]
            return full_text
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
