"""
PDF Reader Service
PDFファイルからテキストを抽出するサービス
"""

from typing import BinaryIO
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError


logger = logging.getLogger(__name__)

# テキスト抽出の最大文字数（トークン節約のため）
MAX_CONTENT_LENGTH = 10000


def extract_text_from_pdf(file: BinaryIO) -> str:
    """
    PDFファイルからテキストを抽出する

    Args:
        file: PDFファイルのバイナリストリーム

    Returns:
        抽出されたテキスト（最大10,000文字）
        読み込み失敗時は空文字を返す
    """
    try:
        reader = PdfReader(file)

        # 暗号化されているかチェック
        if reader.is_encrypted:
            logger.warning("PDF is encrypted and cannot be read")
            raise ValueError("このPDFは暗号化されているため読み込めません。")

        # 全ページからテキストを抽出
        text_parts = []
        for page_num, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"[ページ {page_num + 1}]\n{page_text}")
            except Exception as e:
                logger.warning(f"Failed to extract text from page {page_num + 1}: {e}")
                continue

        if not text_parts:
            logger.warning("No text could be extracted from PDF")
            raise ValueError("PDFからテキストを抽出できませんでした。画像のみのPDFの可能性があります。")

        # テキストを結合
        full_text = "\n\n".join(text_parts)

        # 文字数制限
        if len(full_text) > MAX_CONTENT_LENGTH:
            full_text = full_text[:MAX_CONTENT_LENGTH] + "\n\n...（以下省略）"
            logger.info(f"PDF text truncated to {MAX_CONTENT_LENGTH} characters")

        logger.info(f"Extracted {len(full_text)} characters from PDF ({len(reader.pages)} pages)")
        return full_text.strip()

    except ValueError:
        # 既知のエラー（暗号化など）は再送出
        raise
    except PdfReadError as e:
        logger.error(f"PDF read error: {e}")
        raise ValueError(f"PDFファイルの読み込みに失敗しました: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error reading PDF: {e}")
        raise ValueError(f"PDFの処理中にエラーが発生しました: {str(e)}")


def get_pdf_info(file: BinaryIO) -> dict:
    """
    PDFファイルのメタ情報を取得する

    Args:
        file: PDFファイルのバイナリストリーム

    Returns:
        ページ数、タイトルなどのメタ情報
    """
    try:
        reader = PdfReader(file)

        metadata = reader.metadata or {}

        return {
            "page_count": len(reader.pages),
            "title": metadata.get("/Title", None),
            "author": metadata.get("/Author", None),
            "is_encrypted": reader.is_encrypted,
        }
    except Exception as e:
        logger.error(f"Failed to get PDF info: {e}")
        return {
            "page_count": 0,
            "title": None,
            "author": None,
            "is_encrypted": False,
        }
