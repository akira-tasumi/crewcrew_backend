"""
バックグラウンド実行管理ルーター

/api/background - バックグラウンド実行の開始・状態確認・結果取得
"""

import json
import asyncio
import logging
from datetime import datetime
from typing import Optional, Set

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import (
    User as UserModel,
    Crew as CrewModel,
    BackgroundExecution,
    ExecutionStatus,
    now_jst,
)
from services import notification_service
from services.notification_service import LogAction, LogLevel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/background", tags=["background"])

# キャンセルされた実行IDを追跡するセット
_cancelled_executions: Set[int] = set()


def is_cancelled(execution_id: int) -> bool:
    """実行がキャンセルされたかチェック"""
    return execution_id in _cancelled_executions


def mark_cancelled(execution_id: int):
    """実行をキャンセル済みとしてマーク"""
    _cancelled_executions.add(execution_id)
    logger.info(f"[Background] Execution {execution_id} marked as cancelled")


def clear_cancelled(execution_id: int):
    """キャンセルマークをクリア（完了後のクリーンアップ）"""
    _cancelled_executions.discard(execution_id)


# --- Pydantic Schemas ---

class StartTaskBackgroundRequest(BaseModel):
    """タスクのバックグラウンド実行開始リクエスト"""
    crew_id: int
    task: str
    google_access_token: Optional[str] = None


class StartProjectBackgroundRequest(BaseModel):
    """プロジェクトのバックグラウンド実行開始リクエスト"""
    project_title: str
    description: Optional[str] = None
    user_goal: str
    tasks: list[dict]  # [{role, instruction, assigned_crew_id, assigned_crew_name, assigned_crew_image}]
    input_values: dict = {}
    search_context: Optional[str] = None
    google_access_token: Optional[str] = None


class BackgroundExecutionResponse(BaseModel):
    """バックグラウンド実行レスポンス"""
    id: int
    execution_type: str
    status: str
    current_step: int
    total_steps: int
    progress_message: Optional[str]
    project_title: Optional[str]
    task_content: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class BackgroundExecutionDetailResponse(BaseModel):
    """バックグラウンド実行詳細レスポンス"""
    id: int
    execution_type: str
    status: str
    current_step: int
    total_steps: int
    progress_message: Optional[str]
    result: Optional[str]
    error_message: Optional[str]
    project_title: Optional[str]
    crew_id: Optional[int]
    task_content: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class BackgroundListResponse(BaseModel):
    """バックグラウンド実行一覧レスポンス"""
    executions: list[BackgroundExecutionResponse]
    running_count: int
    total: int


# --- Helper Functions ---

def get_current_user(db: Session = Depends(get_db)) -> UserModel:
    """現在のユーザーを取得（シングルユーザーモード）"""
    user = db.query(UserModel).filter(UserModel.id == 1).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# --- バックグラウンド実行関数 ---

async def execute_task_background(
    execution_id: int,
    crew_id: int,
    task: str,
    google_access_token: Optional[str],
):
    """タスクをバックグラウンドで実行"""
    from database import SessionLocal
    from models import Crew as CrewModel, BackgroundExecution, TaskLog, now_jst
    from services.bedrock_service import execute_task_with_crew

    db = SessionLocal()
    try:
        # 実行レコードを取得
        execution = db.query(BackgroundExecution).filter(
            BackgroundExecution.id == execution_id
        ).first()
        if not execution:
            return

        # キャンセルチェック
        if is_cancelled(execution_id):
            execution.status = ExecutionStatus.CANCELLED
            execution.progress_message = "キャンセルされました"
            execution.completed_at = now_jst()
            db.commit()
            clear_cancelled(execution_id)
            return

        # ステータス更新: 実行中
        execution.status = ExecutionStatus.RUNNING
        execution.started_at = now_jst()
        execution.progress_message = "タスクを実行中..."
        db.commit()

        # クルーを取得
        crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
        if not crew:
            execution.status = ExecutionStatus.FAILED
            execution.error_message = "Crew not found"
            execution.completed_at = now_jst()
            db.commit()
            return

        # タスク実行
        personality = crew.personality or "真面目で丁寧な対応を心がける。"
        result = await execute_task_with_crew(
            crew_name=crew.name,
            crew_role=crew.role,
            personality=personality,
            task=task,
        )

        if result["success"]:
            # EXP付与
            exp_gained = 15
            crew.exp += exp_gained
            if crew.exp >= 100:
                crew.exp -= 100
                crew.level += 1

            # TaskLog保存
            task_log = TaskLog(
                crew_id=crew.id,
                user_input=task,
                ai_response=result["result"] or "",
                exp_gained=exp_gained,
            )
            db.add(task_log)

            # 実行完了
            execution.status = ExecutionStatus.COMPLETED
            execution.result = json.dumps({
                "success": True,
                "result": result["result"],
                "crew_name": crew.name,
                "exp_gained": exp_gained,
            }, ensure_ascii=False)
            execution.progress_message = "タスク完了"
            execution.completed_at = now_jst()

            # 通知作成
            notification_service.create_notification(
                db=db,
                user_id=execution.user_id,
                title="タスク完了",
                message=f"{crew.name}がタスクを完了しました（+{exp_gained}EXP）",
                notification_type="success",
                link="/log",
            )
            notification_service.write_log(
                db=db,
                user_id=execution.user_id,
                action=LogAction.TASK_COMPLETED,
                message=f"バックグラウンドタスク完了: {crew.name}",
                level=LogLevel.INFO,
            )
        else:
            execution.status = ExecutionStatus.FAILED
            execution.error_message = result.get("error", "Unknown error")
            execution.completed_at = now_jst()

            # エラー通知
            notification_service.create_notification(
                db=db,
                user_id=execution.user_id,
                title="タスク失敗",
                message=f"{crew.name}のタスク実行に失敗しました",
                notification_type="error",
                link="/log",
            )

        db.commit()

    except Exception as e:
        # エラー処理
        execution = db.query(BackgroundExecution).filter(
            BackgroundExecution.id == execution_id
        ).first()
        if execution:
            execution.status = ExecutionStatus.FAILED
            execution.error_message = str(e)
            execution.completed_at = now_jst()
            db.commit()
    finally:
        db.close()


async def execute_project_background(
    execution_id: int,
    project_title: str,
    tasks: list[dict],
    input_values: dict,
    search_context: Optional[str],
    google_access_token: Optional[str],
):
    """プロジェクトをバックグラウンドで実行"""
    from database import SessionLocal
    from models import Crew as CrewModel, BackgroundExecution, now_jst
    from services.bedrock_service import execute_task_with_crew
    from graphs.workflow import run_generator_only

    db = SessionLocal()
    try:
        # 実行レコードを取得
        execution = db.query(BackgroundExecution).filter(
            BackgroundExecution.id == execution_id
        ).first()
        if not execution:
            return

        # キャンセルチェック
        if is_cancelled(execution_id):
            execution.status = ExecutionStatus.CANCELLED
            execution.progress_message = "キャンセルされました"
            execution.completed_at = now_jst()
            db.commit()
            clear_cancelled(execution_id)
            return

        # ステータス更新: 実行中
        execution.status = ExecutionStatus.RUNNING
        execution.started_at = now_jst()
        execution.total_steps = len(tasks)
        db.commit()

        # 通知: 開始
        notification_service.create_notification(
            db=db,
            user_id=execution.user_id,
            title="プロジェクト開始",
            message=f"「{project_title}」の実行を開始しました",
            notification_type="info",
            link="/log",
        )
        notification_service.write_log(
            db=db,
            user_id=execution.user_id,
            action=LogAction.PROJECT_STARTED,
            message=f"バックグラウンドプロジェクト開始: {project_title}",
            level=LogLevel.INFO,
        )
        db.commit()

        results = []
        previous_output = ""

        for idx, task_info in enumerate(tasks):
            # キャンセルチェック（各タスク開始前）
            if is_cancelled(execution_id):
                execution.status = ExecutionStatus.CANCELLED
                execution.progress_message = f"タスク {idx + 1} 開始前にキャンセルされました"
                execution.completed_at = now_jst()
                execution.result = json.dumps({
                    "success": False,
                    "cancelled": True,
                    "project_title": project_title,
                    "results": results,
                    "cancelled_at_task": idx,
                }, ensure_ascii=False)
                db.commit()
                clear_cancelled(execution_id)
                return

            # 進捗更新
            execution.current_step = idx + 1
            execution.progress_message = f"タスク {idx + 1}/{len(tasks)} 実行中: {task_info.get('role', 'タスク')}"
            db.commit()

            # クルーを取得
            crew_id = task_info.get("assigned_crew_id")
            crew = db.query(CrewModel).filter(CrewModel.id == crew_id).first()
            if not crew:
                continue

            # タスク構築
            instruction = task_info.get("instruction", "")

            # 入力値の置換
            for key, value in input_values.items():
                placeholder = f"{{{key}}}"
                if placeholder in instruction:
                    instruction = instruction.replace(placeholder, str(value))

            # タスク実行
            if idx > 0 and previous_output:
                full_task = f"""## あなたのタスク
{instruction}

## 前のタスクの結果
{previous_output}

上記の指示に従って、タスクを実行してください。"""
            else:
                search_info = search_context if search_context and idx == 0 else ""
                full_task = f"""## あなたのタスク
{instruction}
{search_info}

上記の指示に従って、タスクを実行してください。"""

            personality = crew.personality or "真面目で丁寧な対応を心がける。"

            try:
                gen_result = await asyncio.to_thread(
                    run_generator_only,
                    task=full_task,
                    crew_name=crew.name,
                    crew_role=crew.role,
                    personality=personality,
                )

                task_result = gen_result.get("result", "")
                score = gen_result.get("score", 100)

                # EXP付与
                exp_gained = 15
                if score >= 90:
                    exp_gained += 20
                elif score >= 70:
                    exp_gained += 10

                crew.exp += exp_gained
                if crew.exp >= 100:
                    crew.exp -= 100
                    crew.level += 1

                results.append({
                    "task_index": idx,
                    "role": task_info.get("role", ""),
                    "crew_name": crew.name,
                    "result": task_result[:500],  # 結果を短縮
                    "score": score,
                    "exp_gained": exp_gained,
                    "status": "completed",
                })

                previous_output = task_result

                # タスク完了通知
                notification_service.write_log(
                    db=db,
                    user_id=execution.user_id,
                    action=LogAction.TASK_COMPLETED,
                    message=f"タスク完了: {task_info.get('role', '')} ({crew.name}) - スコア: {score}",
                    level=LogLevel.INFO,
                )
                db.commit()

            except Exception as e:
                results.append({
                    "task_index": idx,
                    "role": task_info.get("role", ""),
                    "crew_name": crew.name,
                    "error": str(e),
                    "status": "error",
                })

        # 完了
        execution.status = ExecutionStatus.COMPLETED
        execution.result = json.dumps({
            "success": True,
            "project_title": project_title,
            "results": results,
        }, ensure_ascii=False)
        execution.progress_message = "プロジェクト完了"
        execution.completed_at = now_jst()

        # 完了通知
        notification_service.create_notification(
            db=db,
            user_id=execution.user_id,
            title="プロジェクト完了",
            message=f"「{project_title}」が完了しました",
            notification_type="success",
            link="/log",
        )
        notification_service.write_log(
            db=db,
            user_id=execution.user_id,
            action=LogAction.PROJECT_COMPLETED,
            message=f"バックグラウンドプロジェクト完了: {project_title}",
            level=LogLevel.INFO,
        )
        db.commit()

    except Exception as e:
        execution = db.query(BackgroundExecution).filter(
            BackgroundExecution.id == execution_id
        ).first()
        if execution:
            execution.status = ExecutionStatus.FAILED
            execution.error_message = str(e)
            execution.completed_at = now_jst()

            notification_service.create_notification(
                db=db,
                user_id=execution.user_id,
                title="プロジェクト失敗",
                message=f"「{project_title}」の実行中にエラーが発生しました",
                notification_type="error",
                link="/log",
            )
            db.commit()
    finally:
        db.close()


# --- API Endpoints ---

@router.post("/task", response_model=BackgroundExecutionResponse)
async def start_task_background(
    request: StartTaskBackgroundRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """タスクをバックグラウンドで実行開始"""
    # クルー確認
    crew = db.query(CrewModel).filter(CrewModel.id == request.crew_id).first()
    if not crew:
        raise HTTPException(status_code=404, detail="Crew not found")

    # 実行レコード作成
    execution = BackgroundExecution(
        user_id=current_user.id,
        execution_type="task",
        crew_id=request.crew_id,
        task_content=request.task,
        status=ExecutionStatus.PENDING,
        total_steps=1,
        progress_message="準備中...",
    )
    db.add(execution)
    db.commit()
    db.refresh(execution)

    # バックグラウンドタスク登録
    background_tasks.add_task(
        execute_task_background,
        execution_id=execution.id,
        crew_id=request.crew_id,
        task=request.task,
        google_access_token=request.google_access_token,
    )

    return BackgroundExecutionResponse(
        id=execution.id,
        execution_type=execution.execution_type,
        status=execution.status,
        current_step=execution.current_step,
        total_steps=execution.total_steps,
        progress_message=execution.progress_message,
        project_title=execution.project_title,
        task_content=execution.task_content,
        created_at=execution.created_at,
    )


@router.post("/project", response_model=BackgroundExecutionResponse)
async def start_project_background(
    request: StartProjectBackgroundRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """プロジェクトをバックグラウンドで実行開始"""
    # 実行レコード作成
    execution = BackgroundExecution(
        user_id=current_user.id,
        execution_type="project",
        project_title=request.project_title,
        project_data=json.dumps({
            "description": request.description,
            "user_goal": request.user_goal,
            "tasks": request.tasks,
            "input_values": request.input_values,
            "search_context": request.search_context,
        }, ensure_ascii=False),
        status=ExecutionStatus.PENDING,
        total_steps=len(request.tasks),
        progress_message="準備中...",
    )
    db.add(execution)
    db.commit()
    db.refresh(execution)

    # バックグラウンドタスク登録
    background_tasks.add_task(
        execute_project_background,
        execution_id=execution.id,
        project_title=request.project_title,
        tasks=request.tasks,
        input_values=request.input_values,
        search_context=request.search_context,
        google_access_token=request.google_access_token,
    )

    return BackgroundExecutionResponse(
        id=execution.id,
        execution_type=execution.execution_type,
        status=execution.status,
        current_step=execution.current_step,
        total_steps=execution.total_steps,
        progress_message=execution.progress_message,
        project_title=execution.project_title,
        task_content=execution.task_content,
        created_at=execution.created_at,
    )


@router.get("/list", response_model=BackgroundListResponse)
async def list_background_executions(
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """バックグラウンド実行の一覧を取得"""
    query = db.query(BackgroundExecution).filter(
        BackgroundExecution.user_id == current_user.id
    )

    if status:
        query = query.filter(BackgroundExecution.status == status)

    total = query.count()
    # pending, running, cancelling を実行中としてカウント
    running_count = db.query(BackgroundExecution).filter(
        BackgroundExecution.user_id == current_user.id,
        BackgroundExecution.status.in_([
            ExecutionStatus.PENDING,
            ExecutionStatus.RUNNING,
            ExecutionStatus.CANCELLING
        ])
    ).count()

    executions = query.order_by(BackgroundExecution.created_at.desc()).offset(offset).limit(limit).all()

    return BackgroundListResponse(
        executions=[
            BackgroundExecutionResponse(
                id=e.id,
                execution_type=e.execution_type,
                status=e.status,
                current_step=e.current_step,
                total_steps=e.total_steps,
                progress_message=e.progress_message,
                project_title=e.project_title,
                task_content=e.task_content,
                created_at=e.created_at,
            )
            for e in executions
        ],
        running_count=running_count,
        total=total,
    )


@router.get("/{execution_id}", response_model=BackgroundExecutionDetailResponse)
async def get_background_execution(
    execution_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """バックグラウンド実行の詳細を取得"""
    execution = db.query(BackgroundExecution).filter(
        BackgroundExecution.id == execution_id,
        BackgroundExecution.user_id == current_user.id,
    ).first()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    return BackgroundExecutionDetailResponse(
        id=execution.id,
        execution_type=execution.execution_type,
        status=execution.status,
        current_step=execution.current_step,
        total_steps=execution.total_steps,
        progress_message=execution.progress_message,
        result=execution.result,
        error_message=execution.error_message,
        project_title=execution.project_title,
        crew_id=execution.crew_id,
        task_content=execution.task_content,
        created_at=execution.created_at,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
    )


@router.post("/{execution_id}/cancel")
async def cancel_background_execution(
    execution_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """バックグラウンド実行をキャンセル"""
    execution = db.query(BackgroundExecution).filter(
        BackgroundExecution.id == execution_id,
        BackgroundExecution.user_id == current_user.id,
    ).first()

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    # 終了状態の場合はキャンセル不可
    if execution.status in ExecutionStatus.TERMINAL:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel execution with status: {execution.status}"
        )

    # 既にキャンセル処理中の場合
    if execution.status == ExecutionStatus.CANCELLING:
        return {"success": True, "message": "Cancellation already in progress"}

    # 1. インメモリSetに追加（実行ループの即時停止用）
    mark_cancelled(execution_id)

    # 2. DBステータスを「cancelling」に更新（UI表示および再起動時の復元用）
    execution.status = ExecutionStatus.CANCELLING
    execution.progress_message = "キャンセル処理中..."
    db.commit()

    # 通知作成
    notification_service.create_notification(
        db=db,
        user_id=execution.user_id,
        title="キャンセル要求",
        message=f"「{execution.project_title or execution.task_content or 'タスク'}」のキャンセルを要求しました",
        notification_type="warning",
        link="/log",
    )

    logger.info(f"[Background] Execution {execution_id} cancellation requested by user")

    return {"success": True, "message": "Cancellation requested"}
