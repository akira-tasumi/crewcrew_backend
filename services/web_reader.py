"""
Web Reader Service
URLからWebページのコンテンツを取得し、主要なテキストを抽出するサービス
"""

import requests
from bs4 import BeautifulSoup
from typing import Optional
import re


# テキスト抽出の最大文字数（トークン節約のため）
MAX_CONTENT_LENGTH = 8000

# User-Agent（403エラー防止）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# リクエストのタイムアウト（秒）
REQUEST_TIMEOUT = 15


def fetch_web_content(url: str) -> str:
    """
    URLからWebページの主要なテキストコンテンツを取得する

    Args:
        url: 取得するWebページのURL

    Returns:
        抽出されたテキストコンテンツ（最大8000文字）

    Raises:
        ValueError: URLが無効な場合
        requests.RequestException: リクエストに失敗した場合
    """
    # URLのバリデーション
    if not url or not url.startswith(('http://', 'https://')):
        raise ValueError("有効なURLを入力してください（http:// または https:// で始まる必要があります）")

    # HTTPリクエスト
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
        'Accept-Encoding': 'gzip, deflate',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        raise ValueError("リクエストがタイムアウトしました。URLを確認してください。")
    except requests.exceptions.TooManyRedirects:
        raise ValueError("リダイレクトが多すぎます。URLを確認してください。")
    except requests.exceptions.RequestException as e:
        raise ValueError(f"ページの取得に失敗しました: {str(e)}")

    # 文字エンコーディングの検出
    response.encoding = response.apparent_encoding or 'utf-8'

    # HTMLをパース
    soup = BeautifulSoup(response.text, 'html.parser')

    # 不要な要素を削除
    for element in soup.find_all(['script', 'style', 'nav', 'footer', 'header',
                                   'aside', 'noscript', 'iframe', 'form', 'button',
                                   'input', 'select', 'textarea', 'svg', 'img']):
        element.decompose()

    # 広告・ナビゲーション系のクラスを持つ要素も削除
    ad_patterns = ['ad', 'advertisement', 'banner', 'sidebar', 'menu', 'navigation',
                   'social', 'share', 'comment', 'related', 'recommend']
    for pattern in ad_patterns:
        for element in soup.find_all(class_=re.compile(pattern, re.IGNORECASE)):
            element.decompose()
        for element in soup.find_all(id=re.compile(pattern, re.IGNORECASE)):
            element.decompose()

    # メインコンテンツを探す
    content_texts = []

    # タイトルを取得
    title = soup.find('title')
    if title and title.string:
        content_texts.append(f"【タイトル】{title.string.strip()}")

    # OGP descriptionを取得
    og_description = soup.find('meta', property='og:description')
    if og_description and og_description.get('content'):
        content_texts.append(f"【概要】{og_description['content'].strip()}")
    else:
        meta_description = soup.find('meta', attrs={'name': 'description'})
        if meta_description and meta_description.get('content'):
            content_texts.append(f"【概要】{meta_description['content'].strip()}")

    # article, main, または本文っぽい部分を優先的に探す
    main_content = (
        soup.find('article') or
        soup.find('main') or
        soup.find(class_=re.compile('(article|content|post|entry|main)', re.IGNORECASE)) or
        soup.find(id=re.compile('(article|content|post|entry|main)', re.IGNORECASE)) or
        soup.body
    )

    if main_content:
        # 見出しと段落を抽出
        for tag in main_content.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'blockquote']):
            text = tag.get_text(strip=True)
            if text and len(text) > 10:  # 短すぎるテキストは除外
                # 見出しタグにはマーカーを付ける
                if tag.name.startswith('h'):
                    level = int(tag.name[1])
                    prefix = '#' * level + ' '
                    content_texts.append(prefix + text)
                else:
                    content_texts.append(text)

    # テキストを結合
    full_text = '\n\n'.join(content_texts)

    # 連続する空白・改行を正規化
    full_text = re.sub(r'\n{3,}', '\n\n', full_text)
    full_text = re.sub(r' {2,}', ' ', full_text)

    # 文字数制限
    if len(full_text) > MAX_CONTENT_LENGTH:
        full_text = full_text[:MAX_CONTENT_LENGTH] + "\n\n...（以下省略）"

    if not full_text.strip():
        raise ValueError("ページからテキストを抽出できませんでした。")

    return full_text.strip()


def get_page_title(url: str) -> Optional[str]:
    """
    URLからページタイトルのみを取得する（軽量版）

    Args:
        url: 取得するWebページのURL

    Returns:
        ページタイトル、取得できない場合はNone
    """
    try:
        headers = {'User-Agent': USER_AGENT}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or 'utf-8'

        soup = BeautifulSoup(response.text, 'html.parser')
        title = soup.find('title')

        if title and title.string:
            return title.string.strip()
        return None
    except Exception:
        return None
