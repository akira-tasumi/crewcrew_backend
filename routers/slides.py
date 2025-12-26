"""
Google Slides API ルーター

Googleスライドの作成エンドポイントを提供。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.google_slides_service import (
    create_presentation,
    create_presentation_from_summary
)


router = APIRouter(prefix="/api/slides", tags=["slides"])


class CreateSlidesRequest(BaseModel):
    """スライド作成リクエスト"""
    access_token: str = Field(..., description="Google OAuth2アクセストークン")
    title: str = Field(..., description="プレゼンテーションのタイトル")
    pages: list[str] = Field(..., description="各スライドの本文テキストのリスト")


class CreateSlidesResponse(BaseModel):
    """スライド作成レスポンス"""
    success: bool
    presentationId: str | None = None
    presentationUrl: str | None = None
    error: str | None = None


class SummarySection(BaseModel):
    """要約セクション"""
    heading: str = Field(..., description="セクションの見出し")
    content: str = Field(..., description="セクションの内容")


class CreateSlidesFromSummaryRequest(BaseModel):
    """要約からスライド作成リクエスト"""
    access_token: str = Field(..., description="Google OAuth2アクセストークン")
    title: str = Field(..., description="プレゼンテーションのタイトル")
    sections: list[SummarySection] = Field(..., description="要約セクションのリスト")


@router.post("/create", response_model=CreateSlidesResponse)
async def create_slides(request: CreateSlidesRequest):
    """
    Googleスライドを作成する

    - **access_token**: フロントエンドから渡されるGoogle OAuth2アクセストークン
    - **title**: プレゼンテーションのタイトル
    - **pages**: 各スライドの本文テキストのリスト

    Returns:
        presentationId: 作成されたプレゼンテーションのID
        presentationUrl: 作成されたプレゼンテーションのURL
    """
    try:
        if not request.access_token:
            raise HTTPException(status_code=400, detail="access_token is required")

        if not request.title:
            raise HTTPException(status_code=400, detail="title is required")

        if not request.pages or len(request.pages) == 0:
            raise HTTPException(status_code=400, detail="pages must not be empty")

        result = create_presentation(
            access_token=request.access_token,
            title=request.title,
            pages=request.pages
        )

        return CreateSlidesResponse(
            success=True,
            presentationId=result["presentationId"],
            presentationUrl=result["presentationUrl"]
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"[Slides Router] Error creating slides: {e}")
        return CreateSlidesResponse(
            success=False,
            error=str(e)
        )


@router.post("/create-from-summary", response_model=CreateSlidesResponse)
async def create_slides_from_summary(request: CreateSlidesFromSummaryRequest):
    """
    要約セクションからGoogleスライドを作成する

    - **access_token**: フロントエンドから渡されるGoogle OAuth2アクセストークン
    - **title**: プレゼンテーションのタイトル
    - **sections**: 見出しと内容を持つセクションのリスト

    Returns:
        presentationId: 作成されたプレゼンテーションのID
        presentationUrl: 作成されたプレゼンテーションのURL
    """
    try:
        if not request.access_token:
            raise HTTPException(status_code=400, detail="access_token is required")

        if not request.title:
            raise HTTPException(status_code=400, detail="title is required")

        if not request.sections or len(request.sections) == 0:
            raise HTTPException(status_code=400, detail="sections must not be empty")

        # セクションを辞書のリストに変換
        sections_dict = [
            {"heading": s.heading, "content": s.content}
            for s in request.sections
        ]

        result = create_presentation_from_summary(
            access_token=request.access_token,
            title=request.title,
            summary_sections=sections_dict
        )

        return CreateSlidesResponse(
            success=True,
            presentationId=result["presentationId"],
            presentationUrl=result["presentationUrl"]
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"[Slides Router] Error creating slides from summary: {e}")
        return CreateSlidesResponse(
            success=False,
            error=str(e)
        )
