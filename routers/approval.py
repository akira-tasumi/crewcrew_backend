"""
Human-in-the-loop 承認フロー用APIルーター

AIが生成した成果物を外部送信・保存する前に、
人間がレビュー・修正・承認できるエンドポイントを提供する。
"""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import ApprovalRequest, BackgroundExecution, Crew, now_jst
from graphs import (
    run_workflow_with_approval,
    resume_workflow_with_approval,
    get_workflow_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/approval", tags=["approval"])


# =============================================================================
# リクエスト/レスポンスモデル
# =============================================================================

class StartApprovalWorkflowRequest(BaseModel):
    """承認フロー付きワークフロー開始リクエスト"""
    task: str
    crew_id: int
    output_type: str = "slides"  # slides / sheets / slack / email
    max_revisions: int = 3


class ApprovalActionRequest(BaseModel):
    """承認/却下/修正アクションリクエスト"""
    action: str  # approve / reject / modify
    feedback: Optional[str] = None  # コメントや修正指示
    modified_output: Optional[str] = None  # 修正後の成果物


class ApprovalRequestResponse(BaseModel):
    """承認リクエストレスポンス"""
    id: int
    thread_id: str
    output_type: str
    pending_output: str
    preview_data: Optional[str]
    crew_name: str
    crew_image: Optional[str]
    task_summary: str
    status: str
    created_at: datetime


# =============================================================================
# APIエンドポイント
# =============================================================================

@router.post("/start")
async def start_approval_workflow(
    request: StartApprovalWorkflowRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    承認フロー付きでワークフローを開始

    1. クルー情報を取得
    2. ワークフローを実行（human_reviewノードで中断）
    3. 承認待ちリクエストをDBに保存
    4. 承認待ち状態を返却

    Returns:
        承認待ちリクエスト情報（thread_id含む）
    """
    logger.info(f"[Approval] Starting workflow: crew_id={request.crew_id}, output_type={request.output_type}")

    # クルー情報を取得
    crew = db.query(Crew).filter(Crew.id == request.crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    try:
        # ワークフローを実行
        result = await run_workflow_with_approval(
            task=request.task,
            crew_name=crew.name,
            crew_personality=crew.personality or "",
            crew_image=crew.image_url,
            output_type=request.output_type,
            max_revisions=request.max_revisions,
        )

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "Workflow failed"))

        # 承認待ち状態の場合、DBに保存
        if result.get("status") == "awaiting_approval":
            approval_request = ApprovalRequest(
                user_id=1,  # TODO: 認証から取得
                thread_id=result["thread_id"],
                output_type=request.output_type,
                pending_output=result.get("pending_output", ""),
                preview_data=None,  # TODO: プレビューデータ生成
                crew_name=crew.name,
                crew_image=crew.image_url,
                task_summary=request.task[:500],
                status="pending",
            )
            db.add(approval_request)
            db.commit()
            db.refresh(approval_request)

            logger.info(f"[Approval] Created approval request: id={approval_request.id}, thread_id={result['thread_id']}")

            return {
                "success": True,
                "status": "awaiting_approval",
                "approval_request_id": approval_request.id,
                "thread_id": result["thread_id"],
                "pending_output": result.get("pending_output", ""),
                "score": result.get("score", 0),
                "critique": result.get("critique", ""),
                "crew_name": crew.name,
                "crew_image": crew.image_url,
                "output_type": request.output_type,
            }
        else:
            # 承認不要で完了した場合
            return {
                "success": True,
                "status": "completed",
                "thread_id": result.get("thread_id"),
                "final_result": result.get("final_result", ""),
                "score": result.get("score", 0),
                "crew_name": crew.name,
            }

    except Exception as e:
        logger.error(f"[Approval] Error starting workflow: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pending")
async def get_pending_approvals(
    db: Session = Depends(get_db),
):
    """
    承認待ちリクエスト一覧を取得

    Returns:
        承認待ちリクエストのリスト
    """
    approvals = db.query(ApprovalRequest).filter(
        ApprovalRequest.status == "pending"
    ).order_by(ApprovalRequest.created_at.desc()).all()

    return {
        "approvals": [
            {
                "id": a.id,
                "thread_id": a.thread_id,
                "output_type": a.output_type,
                "pending_output": a.pending_output[:500] + "..." if len(a.pending_output) > 500 else a.pending_output,
                "crew_name": a.crew_name,
                "crew_image": a.crew_image,
                "task_summary": a.task_summary,
                "status": a.status,
                "created_at": a.created_at.isoformat(),
            }
            for a in approvals
        ],
        "count": len(approvals),
    }


@router.get("/{request_id}")
async def get_approval_request(
    request_id: int,
    db: Session = Depends(get_db),
):
    """
    承認リクエストの詳細を取得

    Args:
        request_id: 承認リクエストID

    Returns:
        承認リクエストの詳細情報
    """
    approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    return {
        "id": approval.id,
        "thread_id": approval.thread_id,
        "output_type": approval.output_type,
        "pending_output": approval.pending_output,
        "preview_data": approval.preview_data,
        "crew_name": approval.crew_name,
        "crew_image": approval.crew_image,
        "task_summary": approval.task_summary,
        "status": approval.status,
        "human_feedback": approval.human_feedback,
        "modified_output": approval.modified_output,
        "created_at": approval.created_at.isoformat(),
        "reviewed_at": approval.reviewed_at.isoformat() if approval.reviewed_at else None,
    }


@router.post("/{request_id}/approve")
async def approve_request(
    request_id: int,
    db: Session = Depends(get_db),
):
    """
    承認リクエストを承認してワークフローを再開

    Args:
        request_id: 承認リクエストID

    Returns:
        ワークフロー再開結果
    """
    approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    if approval.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request already {approval.status}")

    logger.info(f"[Approval] Approving request: id={request_id}, thread_id={approval.thread_id}")

    try:
        # ディレクターモードで作成された承認リクエストかどうかを判定
        # ディレクターモードのthread_idは "director-v2-..." 形式
        is_director_mode = approval.thread_id.startswith("director-v2-")

        if is_director_mode:
            # ディレクターモードの場合: DBの成果物をそのまま使用
            logger.info(f"[Approval] Director mode approval - using stored output")
            final_result = approval.pending_output
            output_type = approval.output_type
        else:
            # LangGraphワークフローの場合: ワークフローを再開
            result = await resume_workflow_with_approval(
                thread_id=approval.thread_id,
                approval_status="approved",
            )

            if not result.get("success"):
                raise HTTPException(status_code=500, detail=result.get("error", "Resume failed"))

            final_result = result.get("final_result", "")
            output_type = result.get("output_type", "none")

        # DBを更新
        approval.status = "approved"
        approval.reviewed_at = now_jst()
        db.commit()

        logger.info(f"[Approval] Request approved: id={request_id}")

        return {
            "success": True,
            "status": "approved",
            "final_result": final_result,
            "output_type": output_type,
        }

    except Exception as e:
        logger.error(f"[Approval] Error approving request: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{request_id}/reject")
async def reject_request(
    request_id: int,
    request: ApprovalActionRequest,
    db: Session = Depends(get_db),
):
    """
    承認リクエストを却下

    Args:
        request_id: 承認リクエストID
        request: 却下理由等

    Returns:
        却下結果
    """
    approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    if approval.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request already {approval.status}")

    logger.info(f"[Approval] Rejecting request: id={request_id}")

    try:
        # ディレクターモードで作成された承認リクエストかどうかを判定
        is_director_mode = approval.thread_id.startswith("director-v2-")

        if not is_director_mode:
            # LangGraphワークフローの場合: ワークフローを却下状態で再開（終了）
            await resume_workflow_with_approval(
                thread_id=approval.thread_id,
                approval_status="rejected",
                human_feedback=request.feedback,
            )

        # DBを更新
        approval.status = "rejected"
        approval.human_feedback = request.feedback
        approval.reviewed_at = now_jst()
        db.commit()

        logger.info(f"[Approval] Request rejected: id={request_id}")

        return {
            "success": True,
            "status": "rejected",
            "feedback": request.feedback,
        }

    except Exception as e:
        logger.error(f"[Approval] Error rejecting request: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{request_id}/modify")
async def modify_request(
    request_id: int,
    request: ApprovalActionRequest,
    db: Session = Depends(get_db),
):
    """
    承認リクエストを修正してワークフローを再開

    Args:
        request_id: 承認リクエストID
        request: 修正内容

    Returns:
        ワークフロー再開結果
    """
    approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    if approval.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request already {approval.status}")

    if not request.modified_output:
        raise HTTPException(status_code=400, detail="Modified output is required")

    logger.info(f"[Approval] Modifying request: id={request_id}")

    try:
        # ディレクターモードで作成された承認リクエストかどうかを判定
        is_director_mode = approval.thread_id.startswith("director-v2-")

        if is_director_mode:
            # ディレクターモードの場合: 修正後の成果物をそのまま使用
            logger.info(f"[Approval] Director mode modify - using modified output")
            final_result = request.modified_output
            output_type = approval.output_type
        else:
            # LangGraphワークフローの場合: ワークフローを修正状態で再開
            result = await resume_workflow_with_approval(
                thread_id=approval.thread_id,
                approval_status="modified",
                human_feedback=request.feedback,
                modified_output=request.modified_output,
            )

            if not result.get("success"):
                raise HTTPException(status_code=500, detail=result.get("error", "Resume failed"))

            final_result = result.get("final_result", "")
            output_type = result.get("output_type", "none")

        # DBを更新
        approval.status = "modified"
        approval.human_feedback = request.feedback
        approval.modified_output = request.modified_output
        approval.reviewed_at = now_jst()
        db.commit()

        logger.info(f"[Approval] Request modified: id={request_id}")

        return {
            "success": True,
            "status": "modified",
            "final_result": final_result,
            "output_type": output_type,
        }

    except Exception as e:
        logger.error(f"[Approval] Error modifying request: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{request_id}/state")
async def get_workflow_state_endpoint(
    request_id: int,
    db: Session = Depends(get_db),
):
    """
    ワークフローの現在の状態を取得

    Args:
        request_id: 承認リクエストID

    Returns:
        ワークフローの状態
    """
    approval = db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    state = get_workflow_state(approval.thread_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Workflow state not found")

    return {
        "thread_id": approval.thread_id,
        "approval_status": state.get("approval_status", "unknown"),
        "is_complete": state.get("is_complete", False),
        "score": state.get("score", 0),
        "output_type": state.get("output_type", "none"),
        "draft_length": len(state.get("draft", "")),
    }
