"""
YouTube字幕取得サービス

YouTubeの動画URLから字幕（Transcript）を取得する機能を提供。
複数の方法でフォールバック:
1. 外部API (Supadata - AWS環境でも動作)
2. pytubefix
3. InnerTube API
4. ページスクレイピング (ytInitialPlayerResponseから取得)
5. YouTube Data API v3 + timedtext API
6. youtube-transcript-api
7. yt-dlp
"""

import re
import subprocess
import json
import os
import requests
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

# pytubefix をインポート（なければNone）
try:
    from pytubefix import YouTube as PytubeFixYouTube
    PYTUBEFIX_AVAILABLE = True
except ImportError:
    PytubeFixYouTube = None
    PYTUBEFIX_AVAILABLE = False

# YouTube Data API v3 キー（環境変数から取得、なければデフォルト）
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "AIzaSyC_n8S8GymsPstVbqkkMLWQJXYELLtqBWI")

# Supadata API キー（環境変数から取得、デフォルト値あり）
SUPADATA_API_KEY = os.environ.get("SUPADATA_API_KEY", "sd_8b1d6ebe378d6ecdee2f1e1c2289680d")

# RapidAPI キー（環境変数から取得、デフォルト値あり）
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "d505781432mshb63afcf99a713ecp169106jsna86ca3d39a84")


def _fetch_with_supadata(video_id: str) -> list[dict] | None:
    """
    Supadata APIを使って字幕を取得
    https://supadata.ai - 無料枠あり、AWS環境でも動作
    """
    if not SUPADATA_API_KEY:
        print("[YouTube] Supadata: No API key configured")
        return None

    try:
        url = "https://api.supadata.ai/v1/youtube/transcript"
        headers = {
            "x-api-key": SUPADATA_API_KEY,
            "Content-Type": "application/json"
        }
        params = {
            "videoId": video_id,
            "lang": "ja"  # 日本語優先
        }

        response = requests.get(url, headers=headers, params=params, timeout=15)

        if response.status_code == 200:
            data = response.json()
            # Supadataのレスポンス形式に応じて処理
            if "content" in data:
                # 文字列の場合
                if isinstance(data["content"], str):
                    print(f"[YouTube] Supadata: Success (ja)")
                    return [{"text": data["content"]}]
                # リストの場合
                elif isinstance(data["content"], list):
                    texts = [item.get("text", "") for item in data["content"] if item.get("text")]
                    if texts:
                        print(f"[YouTube] Supadata: Success ({len(texts)} segments)")
                        return [{"text": " ".join(texts)}]

            # 日本語がない場合は英語を試す
            params["lang"] = "en"
            response = requests.get(url, headers=headers, params=params, timeout=15)
            if response.status_code == 200:
                data = response.json()
                if "content" in data:
                    if isinstance(data["content"], str):
                        print(f"[YouTube] Supadata: Success (en)")
                        return [{"text": data["content"]}]
                    elif isinstance(data["content"], list):
                        texts = [item.get("text", "") for item in data["content"] if item.get("text")]
                        if texts:
                            print(f"[YouTube] Supadata: Success (en, {len(texts)} segments)")
                            return [{"text": " ".join(texts)}]

        print(f"[YouTube] Supadata: Failed ({response.status_code})")
        return None

    except Exception as e:
        print(f"[YouTube] Supadata error: {e}")
        return None


def _fetch_with_rapidapi(video_id: str) -> list[dict] | None:
    """
    RapidAPI経由でYouTube字幕を取得
    youtube-transcriptor APIを使用
    """
    if not RAPIDAPI_KEY:
        print("[YouTube] RapidAPI: No API key configured")
        return None

    try:
        # YouTube Transcript API (RapidAPI)
        url = "https://youtube-transcriptor.p.rapidapi.com/transcript"
        headers = {
            "X-RapidAPI-Key": RAPIDAPI_KEY,
            "X-RapidAPI-Host": "youtube-transcriptor.p.rapidapi.com"
        }

        # 日本語と英語を試す
        for lang in ["ja", "en"]:
            params = {
                "video_id": video_id,
                "lang": lang
            }

            response = requests.get(url, headers=headers, params=params, timeout=15)

            if response.status_code == 200:
                data = response.json()
                # レスポンス形式: [{"title": "...", "transcriptionAsText": "...", "transcription": [...]}]
                if isinstance(data, list) and len(data) > 0:
                    item = data[0]

                    # transcriptionAsText を優先（テキスト全文）
                    if isinstance(item, dict) and "transcriptionAsText" in item:
                        text = item["transcriptionAsText"]
                        if text and isinstance(text, str) and len(text) > 10:
                            print(f"[YouTube] RapidAPI: Success ({lang}, {len(text)} chars)")
                            return [{"text": text}]

                    # transcription 配列からも試す
                    if isinstance(item, dict) and "transcription" in item:
                        transcription = item["transcription"]
                        if isinstance(transcription, list):
                            texts = []
                            for seg in transcription:
                                if isinstance(seg, dict) and "subtitle" in seg:
                                    texts.append(seg["subtitle"])
                                elif isinstance(seg, str):
                                    texts.append(seg)
                            if texts:
                                print(f"[YouTube] RapidAPI: Success ({lang}, {len(texts)} segments)")
                                return [{"text": " ".join(texts)}]

        print(f"[YouTube] RapidAPI: No transcript found")
        return None

    except Exception as e:
        print(f"[YouTube] RapidAPI error: {e}")
        return None


def _fetch_with_pytubefix(video_id: str) -> list[dict] | None:
    """
    pytubefixを使って字幕を取得する
    最も信頼性が高く、AWS環境でも動作する
    """
    if not PYTUBEFIX_AVAILABLE:
        print("[YouTube] pytubefix not available")
        return None

    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        yt = PytubeFixYouTube(url)

        # 字幕リストを取得
        captions = yt.captions

        # 優先順位: 日本語 → 英語 → 自動生成英語 → 最初の字幕
        caption = None
        for lang_code in ["ja", "en", "a.en"]:
            if lang_code in captions:
                caption = captions[lang_code]
                break

        # 見つからなければ最初の字幕を使用
        if caption is None and len(captions) > 0:
            caption = list(captions)[0]

        if caption is None:
            print(f"[YouTube] pytubefix: No captions found")
            return None

        # SRT形式で字幕を取得
        srt_content = caption.generate_srt_captions()

        # SRTからテキストのみを抽出
        text = _parse_srt(srt_content)

        if text:
            lang_name = getattr(caption, 'name', 'unknown')
            print(f"[YouTube] pytubefix: Found captions ({lang_name})")
            return [{"text": text}]

        return None

    except Exception as e:
        print(f"[YouTube] pytubefix error: {type(e).__name__}: {e}")
        return None


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
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ja,en;q=0.9",
        }

        # 試す言語のリスト
        langs_to_try = [lang] if lang else []
        langs_to_try.extend(["ja", "en", ""])

        for try_lang in langs_to_try:
            # timedtext API URL
            if try_lang:
                url = f"https://www.youtube.com/api/timedtext?v={video_id}&lang={try_lang}&fmt=json3"
            else:
                url = f"https://www.youtube.com/api/timedtext?v={video_id}&fmt=json3"

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


def _fetch_with_innertube(video_id: str) -> list[dict] | None:
    """
    YouTube InnerTube APIを使用して字幕を取得
    複数のクライアントタイプを試す
    """
    # 試すクライアント設定のリスト
    client_configs = [
        # iOS client - 最も緩い制限
        {
            "name": "iOS",
            "payload": {
                "context": {
                    "client": {
                        "hl": "ja",
                        "gl": "JP",
                        "clientName": "IOS",
                        "clientVersion": "19.09.3",
                        "deviceModel": "iPhone14,3",
                        "userAgent": "com.google.ios.youtube/19.09.3 (iPhone14,3; U; CPU iOS 15_6 like Mac OS X)"
                    }
                },
                "videoId": video_id,
                "contentCheckOk": True,
                "racyCheckOk": True
            },
            "headers": {
                "Content-Type": "application/json",
                "User-Agent": "com.google.ios.youtube/19.09.3 (iPhone14,3; U; CPU iOS 15_6 like Mac OS X)",
                "X-YouTube-Client-Name": "5",
                "X-YouTube-Client-Version": "19.09.3",
            }
        },
        # Android client
        {
            "name": "Android",
            "payload": {
                "context": {
                    "client": {
                        "hl": "ja",
                        "gl": "JP",
                        "clientName": "ANDROID",
                        "clientVersion": "19.09.37",
                        "androidSdkVersion": 30,
                        "userAgent": "com.google.android.youtube/19.09.37 (Linux; U; Android 11) gzip"
                    }
                },
                "videoId": video_id,
                "contentCheckOk": True,
                "racyCheckOk": True
            },
            "headers": {
                "Content-Type": "application/json",
                "User-Agent": "com.google.android.youtube/19.09.37 (Linux; U; Android 11) gzip",
                "X-YouTube-Client-Name": "3",
                "X-YouTube-Client-Version": "19.09.37",
            }
        },
        # Web client (TV埋め込み - 制限が緩い)
        {
            "name": "TV Embed",
            "payload": {
                "context": {
                    "client": {
                        "hl": "ja",
                        "gl": "JP",
                        "clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER",
                        "clientVersion": "2.0",
                    }
                },
                "videoId": video_id,
                "contentCheckOk": True,
                "racyCheckOk": True
            },
            "headers": {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
        },
    ]

    api_url = "https://www.youtube.com/youtubei/v1/player"

    for config in client_configs:
        try:
            print(f"[YouTube] InnerTube: Trying {config['name']} client...")

            response = requests.post(
                api_url,
                json=config["payload"],
                headers=config["headers"],
                timeout=15
            )

            if response.status_code != 200:
                print(f"[YouTube] InnerTube {config['name']}: HTTP {response.status_code}")
                continue

            data = response.json()

            # playabilityStatusをチェック
            playability = data.get("playabilityStatus", {})
            status = playability.get("status", "")
            if status != "OK":
                reason = playability.get("reason", "Unknown")
                print(f"[YouTube] InnerTube {config['name']}: {status} - {reason}")
                continue

            # 字幕トラック情報を取得
            captions = data.get("captions", {})
            player_captions = captions.get("playerCaptionsTracklistRenderer", {})
            caption_tracks = player_captions.get("captionTracks", [])

            if not caption_tracks:
                print(f"[YouTube] InnerTube {config['name']}: No caption tracks")
                continue

            # 優先順位: 日本語 → 英語 → 最初のトラック
            selected_track = None
            for preferred_lang in ["ja", "en"]:
                for track in caption_tracks:
                    lang_code = track.get("languageCode", "")
                    if lang_code == preferred_lang:
                        selected_track = track
                        break
                if selected_track:
                    break

            if not selected_track and caption_tracks:
                selected_track = caption_tracks[0]

            if not selected_track:
                continue

            caption_url = selected_track.get("baseUrl", "")
            lang_code = selected_track.get("languageCode", "unknown")

            if not caption_url:
                print(f"[YouTube] InnerTube {config['name']}: No caption URL")
                continue

            print(f"[YouTube] InnerTube {config['name']}: Found {lang_code} caption, fetching...")

            # 字幕をXML形式で取得
            caption_response = requests.get(caption_url, timeout=10)

            if caption_response.status_code != 200:
                print(f"[YouTube] InnerTube: Caption fetch failed: {caption_response.status_code}")
                continue

            # XMLをパース (複数のフォーマットに対応)
            try:
                import xml.etree.ElementTree as ET
                import html as html_module
                root = ET.fromstring(caption_response.text)
                texts = []

                # フォーマット1: <text> 要素
                for text_elem in root.findall('.//text'):
                    text = text_elem.text
                    if text:
                        decoded_text = html_module.unescape(text.strip())
                        if decoded_text:
                            texts.append(decoded_text)

                # フォーマット2: <p> 要素 (timedtext format="3")
                if not texts:
                    for p_elem in root.findall('.//p'):
                        text = p_elem.text
                        if text:
                            decoded_text = html_module.unescape(text.strip())
                            if decoded_text:
                                texts.append(decoded_text)

                if texts:
                    print(f"[YouTube] InnerTube {config['name']}: Success ({len(texts)} segments)")
                    return [{"text": " ".join(texts)}]
            except Exception as e:
                print(f"[YouTube] InnerTube XML parse error: {e}")
                continue

        except Exception as e:
            print(f"[YouTube] InnerTube {config['name']} error: {e}")
            continue

    print(f"[YouTube] InnerTube: All clients failed")
    return None


def _fetch_captions_from_page(video_id: str) -> list[dict] | None:
    """
    YouTubeのページHTMLから字幕URLを抽出して取得する
    ページ内のytInitialPlayerResponseから字幕トラック情報を取得
    """
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ja,en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        }

        response = requests.get(url, headers=headers, timeout=15)

        if response.status_code != 200:
            print(f"[YouTube] Page fetch failed: {response.status_code}")
            return None

        html = response.text

        # ytInitialPlayerResponse を探す（より堅牢なパターン）
        # パターン1: 直接の代入
        start_marker = 'ytInitialPlayerResponse = '
        start_idx = html.find(start_marker)

        if start_idx == -1:
            # パターン2: varを使った代入
            start_marker = 'var ytInitialPlayerResponse = '
            start_idx = html.find(start_marker)

        if start_idx == -1:
            print(f"[YouTube] Could not find ytInitialPlayerResponse in page")
            return None

        start_idx += len(start_marker)

        # ブラケットのバランスを取ってJSON終端を見つける
        bracket_count = 0
        end_idx = start_idx
        in_string = False
        escape_next = False

        for i in range(start_idx, min(start_idx + 500000, len(html))):
            char = html[i]

            if escape_next:
                escape_next = False
                continue

            if char == '\\':
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == '{':
                bracket_count += 1
            elif char == '}':
                bracket_count -= 1
                if bracket_count == 0:
                    end_idx = i + 1
                    break

        if end_idx <= start_idx:
            print(f"[YouTube] Could not find end of ytInitialPlayerResponse JSON")
            return None

        json_str = html[start_idx:end_idx]

        try:
            player_response = json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"[YouTube] Failed to parse player response: {e}")
            return None

        # 字幕トラック情報を取得
        captions = player_response.get("captions", {})
        player_captions = captions.get("playerCaptionsTracklistRenderer", {})
        caption_tracks = player_captions.get("captionTracks", [])

        if not caption_tracks:
            print(f"[YouTube] No caption tracks in player response")
            return None

        # 優先順位: 日本語 → 英語 → 最初のトラック
        selected_track = None
        for preferred_lang in ["ja", "en"]:
            for track in caption_tracks:
                lang_code = track.get("languageCode", "")
                if lang_code == preferred_lang:
                    selected_track = track
                    break
            if selected_track:
                break

        if not selected_track and caption_tracks:
            selected_track = caption_tracks[0]

        if not selected_track:
            return None

        # 字幕URLを取得
        caption_url = selected_track.get("baseUrl", "")
        lang_code = selected_track.get("languageCode", "unknown")

        if not caption_url:
            print(f"[YouTube] No caption URL found")
            return None

        print(f"[YouTube] Found {lang_code} caption track, fetching from: {caption_url[:80]}...")

        # 字幕データを取得（デフォルト形式で試す）
        caption_response = requests.get(caption_url, headers=headers, timeout=10)

        if caption_response.status_code != 200:
            print(f"[YouTube] Caption fetch failed: {caption_response.status_code}")
            return None

        # レスポンスが空かチェック
        if not caption_response.text.strip():
            print(f"[YouTube] Caption response is empty")
            return None

        # まずXMLとして試す（デフォルト形式、複数フォーマット対応）
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(caption_response.text)
            texts = []

            # フォーマット1: <text> 要素
            for text_elem in root.findall('.//text'):
                text = text_elem.text
                if text:
                    # HTMLエンティティをデコード
                    import html
                    decoded_text = html.unescape(text.strip())
                    if decoded_text:
                        texts.append(decoded_text)

            # フォーマット2: <p> 要素 (timedtext format="3")
            if not texts:
                for p_elem in root.findall('.//p'):
                    text = p_elem.text
                    if text:
                        import html
                        decoded_text = html.unescape(text.strip())
                        if decoded_text:
                            texts.append(decoded_text)

            if texts:
                print(f"[YouTube] Page scrape: Found {lang_code} captions (XML format, {len(texts)} segments)")
                return [{"text": " ".join(texts)}]
        except Exception as xml_err:
            print(f"[YouTube] XML parse failed: {xml_err}")

        # JSON形式で再試行
        json_url = caption_url
        if "fmt=" not in json_url:
            json_url += "&fmt=json3"
        else:
            json_url = re.sub(r'fmt=\w+', 'fmt=json3', json_url)

        try:
            json_response = requests.get(json_url, headers=headers, timeout=10)
            if json_response.status_code == 200 and json_response.text.strip():
                caption_data = json_response.json()
                events = caption_data.get("events", [])

                if events:
                    texts = []
                    for event in events:
                        segs = event.get("segs", [])
                        for seg in segs:
                            text = seg.get("utf8", "").strip()
                            if text and text != "\n":
                                texts.append(text)

                    if texts:
                        print(f"[YouTube] Page scrape: Found {lang_code} captions (JSON format, {len(texts)} segments)")
                        return [{"text": " ".join(texts)}]
        except Exception as json_err:
            print(f"[YouTube] JSON parse failed: {json_err}")

        print(f"[YouTube] Page scrape: Could not parse captions")
        return None

    except requests.RequestException as e:
        print(f"[YouTube] Page fetch error: {e}")
        return None
    except Exception as e:
        print(f"[YouTube] Page scrape error: {e}")
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
    1. Supadata API (外部API - AWS環境でも動作)
    2. RapidAPI (外部API - AWS環境でも動作)
    3. pytubefix
    4. InnerTube API (Android clientでAWS環境でも動作する可能性)
    5. ページスクレイピング（ytInitialPlayerResponseから直接URL取得）
    6. YouTube Data API + timedtext API
    7. youtube-transcript-api
    8. yt-dlp フォールバック

    Args:
        video_id: YouTube動画ID

    Returns:
        字幕テキスト（取得できない場合はNone）
    """
    try:
        print(f"[YouTube] Fetching transcript for video_id: {video_id}")

        transcript_data = None
        used_method = None

        # 1. まず外部API（Supadata）を試す - AWS環境でも確実に動作
        print(f"[YouTube] Trying Supadata API...")
        transcript_data = _fetch_with_supadata(video_id)
        if transcript_data:
            used_method = "Supadata API"

        # 2. RapidAPIを試す - AWS環境でも確実に動作
        if not transcript_data:
            print(f"[YouTube] Trying RapidAPI...")
            transcript_data = _fetch_with_rapidapi(video_id)
            if transcript_data:
                used_method = "RapidAPI"

        # 3. pytubefixを試す
        if not transcript_data:
            print(f"[YouTube] Trying pytubefix...")
            transcript_data = _fetch_with_pytubefix(video_id)
            if transcript_data:
                used_method = "pytubefix"

        # 4. InnerTube APIを試す（AWS環境でも動作する可能性が高い）
        if not transcript_data:
            print(f"[YouTube] Trying InnerTube API (Android client)...")
            transcript_data = _fetch_with_innertube(video_id)
            if transcript_data:
                used_method = "InnerTube API"

        # 5. ページスクレイピングを試す
        if not transcript_data:
            print(f"[YouTube] Trying page scraping (ytInitialPlayerResponse)...")
            transcript_data = _fetch_captions_from_page(video_id)
            if transcript_data:
                used_method = "page scraping"

        # 6. YouTube Data API / timedtext APIを試す
        if not transcript_data:
            print(f"[YouTube] Trying YouTube Data API / timedtext...")
            transcript_data = _fetch_with_youtube_data_api(video_id)
            if transcript_data:
                used_method = "YouTube Data API"
            else:
                # timedtext APIを直接試す
                transcript_data = _fetch_captions_via_timedtext(video_id)
                if transcript_data:
                    used_method = "timedtext API"

        # 7. youtube-transcript-api を試す
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

        # 8. yt-dlp フォールバック
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
