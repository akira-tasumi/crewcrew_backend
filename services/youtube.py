"""
YouTube字幕取得サービス

YouTubeの動画URLから字幕（Transcript）を取得する機能を提供。
複数の方法でフォールバック:
1. YouTube Data API v3 (公式API、最も信頼性が高い)
2. youtube-transcript-api (新API)
3. youtube-transcript-api (旧API)
4. yt-dlp (より堅牢、AWS環境での制限回避)
"""

import re
import subprocess
import json
import os
import requests
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

# YouTube Data API v3 キー（環境変数から取得、なければデフォルト）
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "AIzaSyC_n8S8GymsPstVbqkkMLWQJXYELLtqBWI")


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


def _fetch_with_youtube_data_api(video_id: str) -> list[dict] | None:
    """
    YouTube Data API v3を使って字幕を取得する
    公式APIなのでIPブロックされにくい
    """
    if not YOUTUBE_API_KEY:
        print("[YouTube] No API key configured")
        return None

    try:
        # 1. 字幕トラック一覧を取得
        captions_url = "https://www.googleapis.com/youtube/v3/captions"
        params = {
            "part": "snippet",
            "videoId": video_id,
            "key": YOUTUBE_API_KEY
        }

        response = requests.get(captions_url, params=params, timeout=10)

        if response.status_code == 403:
            print(f"[YouTube] Data API: Access forbidden (may need OAuth for caption download)")
            # 字幕ダウンロードにはOAuthが必要なため、代替手段を試す
            return _fetch_captions_via_timedtext(video_id)

        if response.status_code != 200:
            print(f"[YouTube] Data API error: {response.status_code} - {response.text}")
            return None

        data = response.json()
        items = data.get("items", [])

        if not items:
            print(f"[YouTube] Data API: No captions found for video {video_id}")
            return None

        # 日本語 → 英語 → その他の順で優先
        caption_id = None
        caption_lang = None

        for preferred_lang in ["ja", "en"]:
            for item in items:
                lang = item.get("snippet", {}).get("language", "")
                if lang == preferred_lang:
                    caption_id = item.get("id")
                    caption_lang = lang
                    break
            if caption_id:
                break

        # 見つからなければ最初の字幕を使用
        if not caption_id and items:
            caption_id = items[0].get("id")
            caption_lang = items[0].get("snippet", {}).get("language", "unknown")

        if not caption_id:
            return None

        print(f"[YouTube] Data API: Found {caption_lang} caption track")

        # 2. 字幕をダウンロード（注意: 公開字幕のダウンロードにはOAuth認証が必要）
        # 代わりにtimedtext APIを試す
        return _fetch_captions_via_timedtext(video_id, caption_lang)

    except requests.RequestException as e:
        print(f"[YouTube] Data API request error: {e}")
        return None
    except Exception as e:
        print(f"[YouTube] Data API error: {e}")
        return None


def _fetch_captions_via_timedtext(video_id: str, lang: str = None) -> list[dict] | None:
    """
    YouTubeのtimedtext APIを使って字幕を直接取得する
    これは非公式だが、APIキーなしでも動作する場合がある
    """
    try:
        # 試す言語のリスト
        langs_to_try = [lang] if lang else []
        langs_to_try.extend(["ja", "en", ""])

        for try_lang in langs_to_try:
            # timedtext API URL
            if try_lang:
                url = f"https://www.youtube.com/api/timedtext?v={video_id}&lang={try_lang}&fmt=json3"
            else:
                url = f"https://www.youtube.com/api/timedtext?v={video_id}&fmt=json3"

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "ja,en;q=0.9",
            }

            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code == 200 and response.text.strip():
                try:
                    data = response.json()
                    events = data.get("events", [])

                    if events:
                        # 字幕テキストを抽出
                        texts = []
                        for event in events:
                            segs = event.get("segs", [])
                            for seg in segs:
                                text = seg.get("utf8", "").strip()
                                if text and text != "\n":
                                    texts.append(text)

                        if texts:
                            print(f"[YouTube] timedtext API: Found {try_lang or 'auto'} captions ({len(texts)} segments)")
                            return [{"text": " ".join(texts)}]
                except json.JSONDecodeError:
                    continue

        # 自動生成字幕も試す
        for try_lang in ["ja", "en"]:
            url = f"https://www.youtube.com/api/timedtext?v={video_id}&lang={try_lang}&kind=asr&fmt=json3"

            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code == 200 and response.text.strip():
                try:
                    data = response.json()
                    events = data.get("events", [])

                    if events:
                        texts = []
                        for event in events:
                            segs = event.get("segs", [])
                            for seg in segs:
                                text = seg.get("utf8", "").strip()
                                if text and text != "\n":
                                    texts.append(text)

                        if texts:
                            print(f"[YouTube] timedtext API: Found {try_lang} auto-generated captions")
                            return [{"text": " ".join(texts)}]
                except json.JSONDecodeError:
                    continue

        print(f"[YouTube] timedtext API: No captions found")
        return None

    except Exception as e:
        print(f"[YouTube] timedtext API error: {e}")
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
    1. YouTube Data API + timedtext API (AWS環境で最も信頼性が高い)
    2. youtube-transcript-api (日本語 → 英語 → 自動)
    3. yt-dlp フォールバック

    Args:
        video_id: YouTube動画ID

    Returns:
        字幕テキスト（取得できない場合はNone）
    """
    try:
        print(f"[YouTube] Fetching transcript for video_id: {video_id}")

        transcript_data = None
        used_method = None

        # 1. まずYouTube Data API / timedtext APIを試す（AWS環境で有効）
        print(f"[YouTube] Trying YouTube Data API / timedtext...")
        transcript_data = _fetch_with_youtube_data_api(video_id)
        if transcript_data:
            used_method = "YouTube Data API"
        else:
            # timedtext APIを直接試す
            transcript_data = _fetch_captions_via_timedtext(video_id)
            if transcript_data:
                used_method = "timedtext API"

        # 2. youtube-transcript-api を試す
        if not transcript_data:
            print(f"[YouTube] Trying youtube-transcript-api...")
            languages_to_try = [['ja'], ['en'], None]

            for lang in languages_to_try:
                try:
                    transcript_data = _fetch_with_new_api(video_id, lang)
                    if transcript_data is None:
                        transcript_data = _fetch_with_old_api(video_id, lang)

                    if transcript_data:
                        used_method = f"transcript-api ({lang[0] if lang else 'auto'})"
                        break
                except (TranscriptsDisabled, NoTranscriptFound) as e:
                    print(f"[YouTube] transcript-api failed with lang={lang}: {type(e).__name__}")
                    continue
                except Exception as e:
                    print(f"[YouTube] transcript-api error with lang={lang}: {type(e).__name__}: {e}")
                    continue

        # 3. yt-dlp フォールバック
        if not transcript_data:
            print(f"[YouTube] Trying yt-dlp fallback...")
            transcript_data = _fetch_with_ytdlp(video_id)
            if transcript_data:
                used_method = "yt-dlp"

        if not transcript_data:
            print(f"[YouTube] No transcript available for video_id: {video_id}")
            return None

        # 字幕データを連結（辞書のリスト形式）
        full_text = " ".join([snippet['text'] for snippet in transcript_data])

        # トークン制限対策: 長すぎる場合は先頭5000文字でカット
        if len(full_text) > 5000:
            print(f"[YouTube] Transcript truncated from {len(full_text)} to 5000 chars")
            full_text = full_text[:5000]

        print(f"[YouTube] Successfully fetched transcript ({len(full_text)} chars, method: {used_method})")
        return full_text

    except Exception as e:
        print(f"[YouTube] Unexpected error: {e}")
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
