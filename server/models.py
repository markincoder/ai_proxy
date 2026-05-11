import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("is_admin IN (0, 1)", name="ck_users_is_admin_01"),
        CheckConstraint("is_blocked IN (0, 1)", name="ck_users_is_blocked_01"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    vk_user_id: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True)
    yandex_user_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True, nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"))
    is_admin: Mapped[int] = mapped_column(Integer, default=0)
    is_blocked: Mapped[int] = mapped_column(Integer, default=0)
    # Последняя выбранная в чате модель (slug из ai_models); синхронизируется с клиентом.
    last_chat_model_slug: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user")
    payment_orders: Mapped[list["PaymentOrder"]] = relationship(back_populates="user")
    chat_threads: Mapped[list["ChatThread"]] = relationship(back_populates="user")
    support_tickets: Mapped[list["SupportTicket"]] = relationship(back_populates="user")

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
    # Ответы с аудио/речью в чате (GPT Audio и т.п.), не обязательно музыка.
    supports_speech: Mapped[bool] = mapped_column(Boolean, default=False)
    # Распознавание речи (endpoint /audio/transcriptions в OpenRouter), не chat completions.
    supports_transcription: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_coding: Mapped[bool] = mapped_column(Boolean, default=True)
    # Эмбеддинги OpenRouter (/embeddings): отдельные slug; может не участвовать в чате.
    supports_embeddings: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_chat: Mapped[bool] = mapped_column(Boolean, default=True)
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


class SupportTicket(Base):
    """Обращения пользователей в поддержку (переписка)."""

    __tablename__ = "support_tickets"
    __table_args__ = (
        Index("ix_support_tickets_user_updated", "user_id", "updated_at"),
        Index("ix_support_tickets_admin_unread", "admin_unread_count", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    subject: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(24), default="open")
    last_sender_role: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    last_message_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_unread_count: Mapped[int] = mapped_column(Integer, default=0)
    admin_unread_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="support_tickets")
    messages: Mapped[list["SupportMessage"]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="SupportMessage.created_at",
    )


class SupportMessage(Base):
    """Сообщения в обращении пользователя."""

    __tablename__ = "support_messages"
    __table_args__ = (Index("ix_support_messages_ticket_created", "ticket_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ticket_id: Mapped[str] = mapped_column(String(36), ForeignKey("support_tickets.id", ondelete="CASCADE"))
    sender_role: Mapped[str] = mapped_column(String(16))  # user | admin
    sender_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    ticket: Mapped["SupportTicket"] = relationship(back_populates="messages")


class SiteBanner(Base):
    """Системное объявление (техработы): одна строка id=1."""

    __tablename__ = "site_banner"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    schedule_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ErrorLog(Base):
    """Неперехваченные исключения HTTP-запросов для просмотра админом."""

    __tablename__ = "error_logs"
    __table_args__ = (Index("ix_error_logs_created_at", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    context: Mapped[str] = mapped_column(Text)
    error_type: Mapped[str] = mapped_column(String(512))
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    traceback: Mapped[str] = mapped_column(Text)


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


class NewsPost(Base):
    """Новости для страницы /news (дата, заголовок, опционально картинка URL, текст)."""

    __tablename__ = "news_posts"
    __table_args__ = (Index("ix_news_posts_published_at", "published_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    title: Mapped[str] = mapped_column(String(512))
    image_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    body: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserApiKey(Base):
    """Ключ доступа к HTTP API (OpenRouter через прокси); в БД только хэш."""

    __tablename__ = "user_api_keys"
    __table_args__ = (
        Index("ix_user_api_keys_user_id", "user_id"),
        Index("ix_user_api_keys_key_hash", "key_hash", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    key_prefix: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
