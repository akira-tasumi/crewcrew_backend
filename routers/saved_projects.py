"""
保存されたプロジェクトテンプレート管理ルーター

/api/saved-projects - プロジェクトテンプレートのCRUDと再実行
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import SavedProject, Crew, now_jst

router = APIRouter(prefix="/api/saved-projects", tags=["saved-projects"])


# --- Pydantic Schemas ---

class SavedProjectCreate(BaseModel):
    """プロジェクト保存リクエスト"""
    title: str
    description: Optional[str] = None
    prompt_template: str
    crew_id: Optional[int] = None


class SavedProjectUpdate(BaseModel):
    """プロジェクト更新リクエスト"""
    title: Optional[str] = None
    description: Optional[str] = None
    prompt_template: Optional[str] = None
    crew_id: Optional[int] = None
    is_favorite: Optional[bool] = None


class SavedProjectResponse(BaseModel):
    """プロジェクトレスポンス"""
    id: int
    title: str
    description: Optional[str]
    prompt_template: str
    crew_id: Optional[int]
    crew_name: Optional[str] = None
    is_favorite: bool
    run_count: int
    last_run_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RunProjectResponse(BaseModel):
    """プロジェクト実行レスポンス"""
    success: bool
    prompt_template: str
    crew_id: Optional[int]
    crew_name: Optional[str]


# --- API Endpoints ---

@router.post("", response_model=SavedProjectResponse)
async def create_saved_project(
    data: SavedProjectCreate,
    db: Session = Depends(get_db),
):
    """
    新規プロジェクトを保存
    """
    # クルーの存在確認
    crew_name = None
    if data.crew_id:
        crew = db.query(Crew).filter(Crew.id == data.crew_id).first()
        if not crew:
            raise HTTPException(status_code=404, detail="Crew not found")
        crew_name = crew.name

    project = SavedProject(
        user_id=1,  # シングルユーザーモード
        title=data.title,
        description=data.description,
        prompt_template=data.prompt_template,
        crew_id=data.crew_id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    return SavedProjectResponse(
        id=project.id,
        title=project.title,
        description=project.description,
        prompt_template=project.prompt_template,
        crew_id=project.crew_id,
        crew_name=crew_name,
        is_favorite=project.is_favorite,
        run_count=project.run_count,
        last_run_at=project.last_run_at,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("", response_model=list[SavedProjectResponse])
async def list_saved_projects(
    db: Session = Depends(get_db),
):
    """
    保存されたプロジェクト一覧を取得（お気に入り優先、作成日降順）
    """
    projects = db.query(SavedProject).filter(
        SavedProject.user_id == 1
    ).order_by(
        SavedProject.is_favorite.desc(),
        SavedProject.created_at.desc()
    ).all()

    result = []
    for p in projects:
        crew_name = None
        if p.crew_id:
            crew = db.query(Crew).filter(Crew.id == p.crew_id).first()
            if crew:
                crew_name = crew.name

        result.append(SavedProjectResponse(
            id=p.id,
            title=p.title,
            description=p.description,
            prompt_template=p.prompt_template,
            crew_id=p.crew_id,
            crew_name=crew_name,
            is_favorite=p.is_favorite,
            run_count=p.run_count,
            last_run_at=p.last_run_at,
            created_at=p.created_at,
            updated_at=p.updated_at,
        ))

    return result


@router.get("/{project_id}", response_model=SavedProjectResponse)
async def get_saved_project(
    project_id: int,
    db: Session = Depends(get_db),
):
    """
    プロジェクト詳細を取得
    """
    project = db.query(SavedProject).filter(
        SavedProject.id == project_id,
        SavedProject.user_id == 1
    ).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    crew_name = None
    if project.crew_id:
        crew = db.query(Crew).filter(Crew.id == project.crew_id).first()
        if crew:
            crew_name = crew.name

    return SavedProjectResponse(
        id=project.id,
        title=project.title,
        description=project.description,
        prompt_template=project.prompt_template,
        crew_id=project.crew_id,
        crew_name=crew_name,
        is_favorite=project.is_favorite,
        run_count=project.run_count,
        last_run_at=project.last_run_at,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.put("/{project_id}", response_model=SavedProjectResponse)
async def update_saved_project(
    project_id: int,
    data: SavedProjectUpdate,
    db: Session = Depends(get_db),
):
    """
    プロジェクトを更新
    """
    project = db.query(SavedProject).filter(
        SavedProject.id == project_id,
        SavedProject.user_id == 1
    ).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if data.title is not None:
        project.title = data.title
    if data.description is not None:
        project.description = data.description
    if data.prompt_template is not None:
        project.prompt_template = data.prompt_template
    if data.crew_id is not None:
        project.crew_id = data.crew_id
    if data.is_favorite is not None:
        project.is_favorite = data.is_favorite

    db.commit()
    db.refresh(project)

    crew_name = None
    if project.crew_id:
        crew = db.query(Crew).filter(Crew.id == project.crew_id).first()
        if crew:
            crew_name = crew.name

    return SavedProjectResponse(
        id=project.id,
        title=project.title,
        description=project.description,
        prompt_template=project.prompt_template,
        crew_id=project.crew_id,
        crew_name=crew_name,
        is_favorite=project.is_favorite,
        run_count=project.run_count,
        last_run_at=project.last_run_at,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.delete("/{project_id}")
async def delete_saved_project(
    project_id: int,
    db: Session = Depends(get_db),
):
    """
    プロジェクトを削除
    """
    project = db.query(SavedProject).filter(
        SavedProject.id == project_id,
        SavedProject.user_id == 1
    ).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    db.delete(project)
    db.commit()

    return {"success": True, "message": "Project deleted"}


@router.post("/{project_id}/run", response_model=RunProjectResponse)
async def run_saved_project(
    project_id: int,
    db: Session = Depends(get_db),
):
    """
    保存されたプロジェクトを実行（プロンプトとクルー情報を返す）

    フロントエンドはこのレスポンスを使って、ダッシュボードで
    タスクを自動実行する
    """
    project = db.query(SavedProject).filter(
        SavedProject.id == project_id,
        SavedProject.user_id == 1
    ).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # 実行回数と最終実行日時を更新
    project.run_count += 1
    project.last_run_at = now_jst()
    db.commit()

    # クルー名を取得
    crew_name = None
    if project.crew_id:
        crew = db.query(Crew).filter(Crew.id == project.crew_id).first()
        if crew:
            crew_name = crew.name

    return RunProjectResponse(
        success=True,
        prompt_template=project.prompt_template,
        crew_id=project.crew_id,
        crew_name=crew_name,
    )


@router.post("/{project_id}/favorite")
async def toggle_favorite(
    project_id: int,
    db: Session = Depends(get_db),
):
    """
    お気に入りフラグをトグル
    """
    project = db.query(SavedProject).filter(
        SavedProject.id == project_id,
        SavedProject.user_id == 1
    ).first()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project.is_favorite = not project.is_favorite
    db.commit()

    return {"success": True, "is_favorite": project.is_favorite}
