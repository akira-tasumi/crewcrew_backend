"""
Deep Research APIルーター

自律リサーチ機能のエンドポイントを提供
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from graphs.research_graph import run_deep_research, run_deep_research_stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", tags=["research"])


class ResearchRequest(BaseModel):
    """リサーチリクエスト"""
    query: str


class ResearchSource(BaseModel):
    """情報源"""
    title: str
    url: str


class ResearchResponse(BaseModel):
    """リサーチレスポンス"""
    success: bool
    answer: str
    search_queries: list[str]
    sources: list[ResearchSource]
    loop_count: int
    error: Optional[str] = None


@router.post("", response_model=ResearchResponse)
async def deep_research(request: ResearchRequest):
    """
    Deep Research実行（非ストリーミング）

    ユーザーの質問に対して、AIが自律的に検索・情報収集を行い、
    包括的な回答を生成します。

    Args:
        request: リサーチリクエスト（query: 質問文）

    Returns:
        ResearchResponse: 回答と調査プロセス
    """
    if not request.query or len(request.query.strip()) < 5:
        raise HTTPException(status_code=400, detail="質問は5文字以上で入力してください")

    logger.info(f"[Research API] Starting research: {request.query[:50]}...")

    result = await run_deep_research(request.query)

    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "リサーチ中にエラーが発生しました"))

    return ResearchResponse(
        success=result["success"],
        answer=result["answer"],
        search_queries=result["search_queries"],
        sources=[ResearchSource(**s) for s in result["sources"]],
        loop_count=result["loop_count"],
        error=result.get("error"),
    )


@router.post("/stream")
async def deep_research_stream(request: ResearchRequest):
    """
    Deep Research実行（SSEストリーミング）

    リサーチの進捗をリアルタイムで通知します。

    イベント種類:
    - research_start: リサーチ開始
    - search_complete: 検索完了（各ループ）
    - writing: 回答作成中
    - research_complete: リサーチ完了（最終回答含む）
    - research_error: エラー発生

    Args:
        request: リサーチリクエスト（query: 質問文）

    Returns:
        StreamingResponse: SSEストリーム
    """
    if not request.query or len(request.query.strip()) < 5:
        raise HTTPException(status_code=400, detail="質問は5文字以上で入力してください")

    logger.info(f"[Research API] Starting streaming research: {request.query[:50]}...")

    async def generate():
        try:
            async for event in run_deep_research_stream(request.query):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"[Research API] Stream error: {e}")
            yield f"data: {json.dumps({'type': 'research_error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )
