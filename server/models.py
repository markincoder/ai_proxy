import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    phone: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True)
    phone_verified: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    vk_user_id: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True)
    yandex_user_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user")
    payment_orders: Mapped[list["PaymentOrder"]] = relationship(back_populates="user")
    chat_threads: Mapped[list["ChatThread"]] = relationship(back_populates="user")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AiModel(Base):
    __tablename__ = "ai_models"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(128))
    input_price_per_mn: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    output_price_per_mn: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    fixed_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Понимание изображений на входе (прикреплённое фото в чате), не генерация.
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_image_generation: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_music_generation: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_video_generation: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_coding: Mapped[bool] = mapped_column(Boolean, default=True)
    # Тариф OpenRouter :free — показываем в категории «Бесплатные», цены в БД 0.
    is_free: Mapped[bool] = mapped_column(Boolean, default=False)
    # Краткое описание для карточки выбора модели (русский текст).
    description_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Как считается оплата для нестандартных моделей (видео/картинки/аудио) — для страницы тарифов.
    pricing_note_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatThread(Base):
    """Сохранённые диалоги пользователя (история как в ChatGPT)."""

    __tablename__ = "chat_threads"
    __table_args__ = (Index("ix_chat_threads_user_updated", "user_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(512), default="Новый чат")
    model_slug: Mapped[str] = mapped_column(String(255))
    messages: Mapped[list[Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="chat_threads")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (Index("ix_transactions_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(16))  # DEPOSIT | SPEND
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    openrouter_request_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="transactions")


class PaymentOrder(Base):
    __tablename__ = "payment_orders"
    __table_args__ = (Index("ix_payment_user_status", "user_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    yookassa_payment_id: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="payment_orders")
