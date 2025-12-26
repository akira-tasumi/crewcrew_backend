"""
ショップ機能ルーター

- ガジェット（コイン消費）
- 特殊性格（ルビー消費）
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import (
    User as UserModel,
    Gadget,
    UserGadget,
    PersonalityItem,
    UnlockedPersonality,
    CrewGadget,
    Crew,
)

router = APIRouter(prefix="/api/shop", tags=["shop"])


# --- Pydantic Schemas ---

class GadgetItemResponse(BaseModel):
    """ガジェット商品"""
    id: int
    name: str
    description: str
    icon: str
    effect_type: str
    base_effect_value: int
    price: int  # base_costを使用
    is_owned: bool  # 所持済みか
    equipped_by: str | None  # 装備中のクルー名（あれば）

    class Config:
        from_attributes = True


class PersonalityItemResponse(BaseModel):
    """性格商品"""
    id: int
    personality_key: str
    name: str
    description: str
    emoji: str
    ruby_price: int
    is_owned: bool  # アンロック済みか

    class Config:
        from_attributes = True


class ShopResponse(BaseModel):
    """ショップ全体のレスポンス"""
    gadgets: list[GadgetItemResponse]
    personalities: list[PersonalityItemResponse]
    user_coin: int
    user_ruby: int


class PurchaseResponse(BaseModel):
    """購入結果"""
    success: bool
    message: str
    new_coin: int | None = None
    new_ruby: int | None = None


class EquippedGadgetInfo(BaseModel):
    """装備中ガジェット情報"""
    gadget_id: int
    crew_id: int
    crew_name: str
    slot_index: int


class UserGadgetResponse(BaseModel):
    """ユーザー所持ガジェット"""
    id: int
    name: str
    description: str
    icon: str
    effect_type: str
    base_effect_value: int
    level: int  # 強化レベル
    equipped_by: EquippedGadgetInfo | None  # 装備中の情報


# --- Helper Functions ---

def get_current_user(db: Session = Depends(get_db)) -> UserModel:
    """現在のユーザーを取得"""
    user = db.query(UserModel).filter(UserModel.id == 1).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# --- API Endpoints ---

@router.get("/items", response_model=ShopResponse)
async def get_shop_items(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> ShopResponse:
    """
    ショップの商品一覧を取得
    """
    # ガジェット一覧
    gadgets = db.query(Gadget).all()
    owned_gadget_ids = {
        ug.gadget_id
        for ug in db.query(UserGadget).filter(UserGadget.user_id == current_user.id).all()
    }

    # 装備中のガジェット情報を取得
    equipped_gadgets = (
        db.query(CrewGadget, Crew)
        .join(Crew, CrewGadget.crew_id == Crew.id)
        .all()
    )
    equipped_map = {cg.gadget_id: crew.name for cg, crew in equipped_gadgets}

    gadget_items = [
        GadgetItemResponse(
            id=g.id,
            name=g.name,
            description=g.description,
            icon=g.icon,
            effect_type=g.effect_type,
            base_effect_value=g.base_effect_value,
            price=g.base_cost,
            is_owned=g.id in owned_gadget_ids,
            equipped_by=equipped_map.get(g.id),
        )
        for g in gadgets
    ]

    # 性格一覧
    personalities = db.query(PersonalityItem).all()
    unlocked_keys = {
        up.personality_key
        for up in db.query(UnlockedPersonality).filter(UnlockedPersonality.user_id == current_user.id).all()
    }

    personality_items = [
        PersonalityItemResponse(
            id=p.id,
            personality_key=p.personality_key,
            name=p.name,
            description=p.description,
            emoji=p.emoji,
            ruby_price=p.ruby_price,
            is_owned=p.personality_key in unlocked_keys,
        )
        for p in personalities
    ]

    return ShopResponse(
        gadgets=gadget_items,
        personalities=personality_items,
        user_coin=current_user.coin,
        user_ruby=current_user.ruby,
    )


@router.post("/purchase/gadget/{gadget_id}", response_model=PurchaseResponse)
async def purchase_gadget(
    gadget_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> PurchaseResponse:
    """
    ガジェットを購入（コイン消費）
    """
    # ガジェット存在確認
    gadget = db.query(Gadget).filter(Gadget.id == gadget_id).first()
    if not gadget:
        raise HTTPException(status_code=404, detail="Gadget not found")

    # 既に所持しているか確認
    existing = (
        db.query(UserGadget)
        .filter(UserGadget.user_id == current_user.id, UserGadget.gadget_id == gadget_id)
        .first()
    )
    if existing:
        return PurchaseResponse(
            success=False,
            message="既に所持しています",
            new_coin=current_user.coin,
        )

    # コイン残高確認
    if current_user.coin < gadget.base_cost:
        return PurchaseResponse(
            success=False,
            message=f"コインが足りません（必要: {gadget.base_cost}、所持: {current_user.coin}）",
            new_coin=current_user.coin,
        )

    # 購入処理
    current_user.coin -= gadget.base_cost
    user_gadget = UserGadget(user_id=current_user.id, gadget_id=gadget_id)
    db.add(user_gadget)
    db.commit()

    return PurchaseResponse(
        success=True,
        message=f"「{gadget.name}」を購入しました！",
        new_coin=current_user.coin,
    )


@router.post("/purchase/personality/{personality_id}", response_model=PurchaseResponse)
async def purchase_personality(
    personality_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> PurchaseResponse:
    """
    特殊性格をアンロック（ルビー消費）
    """
    # 性格存在確認
    personality = db.query(PersonalityItem).filter(PersonalityItem.id == personality_id).first()
    if not personality:
        raise HTTPException(status_code=404, detail="Personality not found")

    # 既にアンロック済みか確認
    existing = (
        db.query(UnlockedPersonality)
        .filter(
            UnlockedPersonality.user_id == current_user.id,
            UnlockedPersonality.personality_key == personality.personality_key,
        )
        .first()
    )
    if existing:
        return PurchaseResponse(
            success=False,
            message="既にアンロック済みです",
            new_ruby=current_user.ruby,
        )

    # ルビー残高確認
    if current_user.ruby < personality.ruby_price:
        return PurchaseResponse(
            success=False,
            message=f"ルビーが足りません（必要: {personality.ruby_price}、所持: {current_user.ruby}）",
            new_ruby=current_user.ruby,
        )

    # 購入処理
    current_user.ruby -= personality.ruby_price
    unlocked = UnlockedPersonality(
        user_id=current_user.id,
        personality_key=personality.personality_key,
    )
    db.add(unlocked)
    db.commit()

    return PurchaseResponse(
        success=True,
        message=f"性格「{personality.name}」をアンロックしました！",
        new_ruby=current_user.ruby,
    )


@router.get("/my-gadgets", response_model=list[UserGadgetResponse])
async def get_my_gadgets(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
) -> list[UserGadgetResponse]:
    """
    ユーザーが所持しているガジェット一覧を取得
    """
    # 所持ガジェット取得
    user_gadgets = (
        db.query(UserGadget, Gadget)
        .join(Gadget, UserGadget.gadget_id == Gadget.id)
        .filter(UserGadget.user_id == current_user.id)
        .all()
    )

    # 装備中情報を取得
    equipped_gadgets = (
        db.query(CrewGadget, Crew)
        .join(Crew, CrewGadget.crew_id == Crew.id)
        .all()
    )
    equipped_map = {
        cg.gadget_id: EquippedGadgetInfo(
            gadget_id=cg.gadget_id,
            crew_id=cg.crew_id,
            crew_name=crew.name,
            slot_index=cg.slot_index,
        )
        for cg, crew in equipped_gadgets
    }

    return [
        UserGadgetResponse(
            id=gadget.id,
            name=gadget.name,
            description=gadget.description,
            icon=gadget.icon,
            effect_type=gadget.effect_type,
            base_effect_value=gadget.base_effect_value,
            level=ug.level,
            equipped_by=equipped_map.get(gadget.id),
        )
        for ug, gadget in user_gadgets
    ]
