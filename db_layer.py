# ============================
# File: db_layer.py
# Purpose: SQLAlchemy DB setup + schemas + helper CRUD + balance sync
# ============================
from __future__ import annotations
from pathlib import Path
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    create_engine, String, Float, Integer, DateTime, Text, ForeignKey, Numeric, Index
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.sql import func

ROOT_DIR = Path(__file__).parent.resolve()
DB_PATH = ROOT_DIR / "chase.sqlite3"
DB_URL = f"sqlite:///{DB_PATH.as_posix()}"

class Base(DeclarativeBase):
    pass

engine = create_engine(DB_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

# -----------------------------
# Tables
# -----------------------------
class Schedule(Base):
    __tablename__ = "schedules"
    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    market_id: Mapped[Optional[str]] = mapped_column(String(64))
    race_name: Mapped[Optional[str]] = mapped_column(String(256))
    track: Mapped[Optional[str]] = mapped_column(String(256))
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), default="scheduled")
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), onupdate=func.now())
    bets: Mapped[list["Bet"]] = relationship(back_populates="schedule", cascade="all, delete-orphan")

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
    result: Mapped[str] = mapped_column(String(2), default="P")
    profit: Mapped[float] = mapped_column(Float, default=0.0)
    balance_after: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), onupdate=func.now())
    schedule: Mapped[Schedule] = relationship(back_populates="bets")

class BotBalance(Base):
    __tablename__ = "bot_balance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current_balance: Mapped[float] = mapped_column(Numeric(12, 2), default=0.00)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

# -----------------------------
# Init
# -----------------------------
def init_db():
    Base.metadata.create_all(engine)
    # Ensure one row exists in bot_balance
    with SessionLocal() as s:
        if not s.get(BotBalance, 1):
            s.add(BotBalance(id=1, current_balance=0.00))
            s.commit()

# -----------------------------
# Balance helpers
# -----------------------------
def get_balance() -> float:
    with SessionLocal() as s:
        row = s.get(BotBalance, 1)
        return float(row.current_balance) if row else 0.0

def update_balance(new_balance: float):
    with SessionLocal() as s:
        bal = s.get(BotBalance, 1)
        if not bal:
            bal = BotBalance(id=1, current_balance=new_balance)
            s.add(bal)
        else:
            bal.current_balance = new_balance
            bal.updated_at = datetime.utcnow()
        s.commit()

# -----------------------------
# Core CRUD helpers
# -----------------------------
def record_schedule(job_id: str, market, run_at: datetime, status="scheduled", error=None):
    with SessionLocal() as s:
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

def update_schedule_status(job_id: str, status: str, error: str | None = None):
    with SessionLocal() as s:
        sched = s.get(Schedule, job_id)
        if not sched:
            return
        sched.status = status
        if error:
            sched.error = (sched.error + "\n" if sched.error else "") + error
        s.commit()

def create_pending_bet(job_id: str, market, leg: int, selection: str, odds: float, stake: float) -> int:
    with SessionLocal() as s:
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

def finalize_bet(bet_id: int, result_code: str, profit: float, balance_after: float):
    with SessionLocal() as s:
        bet = s.get(Bet, bet_id)
        if not bet:
            return
        bet.result = result_code
        bet.profit = profit
        bet.balance_after = balance_after
        s.commit()
