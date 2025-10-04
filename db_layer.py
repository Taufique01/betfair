# ============================
# File: db_layer.py
# Purpose: SQLAlchemy DB setup + schemas + helper CRUD
# ============================
from __future__ import annotations
from pathlib import Path
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    create_engine, String, Float, Integer, DateTime, Text, ForeignKey,
    Index
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.sql import func

# ---- Configure SQLite path/URL ----
ROOT_DIR = Path(__file__).parent.resolve()
DB_PATH = ROOT_DIR / "chase.sqlite3"
DB_URL = f"sqlite:///{DB_PATH.as_posix()}"

# ---- Base / Engine / Session ----
class Base(DeclarativeBase):
    pass

engine = create_engine(DB_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

# ---- Schemas ----
class Schedule(Base):
    __tablename__ = "schedules"

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    market_id: Mapped[Optional[str]] = mapped_column(String(64))
    race_name: Mapped[Optional[str]] = mapped_column(String(256))
    track: Mapped[Optional[str]] = mapped_column(String(256))
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    status: Mapped[str] = mapped_column(String(32), default="scheduled")  # scheduled|running|done|skipped|error
    error: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), onupdate=func.now())

    bets: Mapped[list["Bet"]] = relationship(back_populates="schedule", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_schedules_market_run", "market_id", "run_at"),
    )

class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(128), ForeignKey("schedules.job_id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    market_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    race_name: Mapped[Optional[str]] = mapped_column(String(256))
    track: Mapped[Optional[str]] = mapped_column(String(256))
    race_datetime: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    leg: Mapped[int] = mapped_column(Integer)
    selection: Mapped[Optional[str]] = mapped_column(String(256))
    odds: Mapped[float] = mapped_column(Float)
    stake: Mapped[float] = mapped_column(Float)

    result: Mapped[str] = mapped_column(String(2), default="P")  # P=Pending, W, L
    profit: Mapped[float] = mapped_column(Float, default=0.0)
    balance_after: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), onupdate=func.now())

    schedule: Mapped[Schedule] = relationship(back_populates="bets")

    __table_args__ = (
        Index("ix_bets_market_time", "market_id", "race_datetime"),
    )

# ---- Init ----
def init_db():
    """Create tables if they don't exist; log path for visibility."""
    Base.metadata.create_all(engine)

# ---- Helper CRUD for main flow ----
def record_schedule(job_id: str, market, run_at: datetime, status: str = "scheduled", error: str | None = None):
    from sqlalchemy.exc import SQLAlchemyError
    with SessionLocal() as s:
        try:
            sched = Schedule(
                job_id=job_id,
                market_id=getattr(market, "market_id", None),
                race_name=getattr(market, "market_name", None),
                track=getattr(getattr(market, "event", None), "name", None),
                run_at=run_at,
                status=status,
                error=error,
            )
            s.add(sched)
            s.commit()
        except SQLAlchemyError:
            s.rollback()
            raise

def update_schedule_status(job_id: str, status: str, error: str | None = None):
    from sqlalchemy.exc import SQLAlchemyError
    with SessionLocal() as s:
        try:
            sched = s.get(Schedule, job_id)
            if not sched:
                return
            sched.status = status
            if error:
                sched.error = (sched.error + "\n" if sched.error else "") + error
            s.commit()
        except SQLAlchemyError:
            s.rollback()
            raise

def create_pending_bet(job_id: str, market, leg: int, selection: str, odds: float, stake: float) -> int:
    from sqlalchemy.exc import SQLAlchemyError
    with SessionLocal() as s:
        try:
            bet = Bet(
                job_id=job_id,
                market_id=getattr(market, "market_id", None),
                race_name=getattr(market, "market_name", None),
                track=getattr(getattr(market, "event", None), "name", None),
                race_datetime=getattr(market, "market_start_time", None),
                leg=leg,
                selection=selection,
                odds=float(odds),
                stake=float(stake),
                result="P",
            )
            s.add(bet)
            s.commit()
            return bet.id
        except SQLAlchemyError:
            s.rollback()
            raise

def finalize_bet(bet_id: int, result_code: str, profit: float, balance_after: float):
    from sqlalchemy.exc import SQLAlchemyError
    with SessionLocal() as s:
        try:
            bet = s.get(Bet, bet_id)
            if not bet:
                return
            bet.result = result_code
            bet.profit = float(profit)
            bet.balance_after = float(balance_after)
            s.commit()
        except SQLAlchemyError:
            s.rollback()
            raise


