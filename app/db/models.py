from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'receptionist', 'doctor')",
            name="ck_users_role",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    practitioner: Mapped[Practitioner | None] = relationship(
        back_populates="user", uselist=False
    )
    created_patients: Mapped[list[Patient]] = relationship(
        back_populates="creator", foreign_keys="Patient.created_by_user_id"
    )
    created_appointments: Mapped[list[Appointment]] = relationship(
        back_populates="creator", foreign_keys="Appointment.created_by_user_id"
    )


class Practitioner(Base):
    __tablename__ = "practitioners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    specialty: Mapped[str | None] = mapped_column(String(120), nullable=True)

    user: Mapped[User] = relationship(back_populates="practitioner")
    working_hours: Mapped[list[WorkingHour]] = relationship(
        back_populates="practitioner", cascade="all, delete-orphan"
    )
    appointments: Mapped[list[Appointment]] = relationship(
        back_populates="practitioner"
    )
    queue_entries: Mapped[list[QueueEntry]] = relationship(
        back_populates="practitioner"
    )


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    creator: Mapped[User] = relationship(
        back_populates="created_patients", foreign_keys=[created_by_user_id]
    )
    appointments: Mapped[list[Appointment]] = relationship(
        back_populates="patient"
    )
    queue_entries: Mapped[list[QueueEntry]] = relationship(
        back_populates="patient"
    )


class WorkingHour(Base):
    __tablename__ = "working_hours"
    __table_args__ = (
        UniqueConstraint(
            "practitioner_id", "weekday", name="uq_working_hours_day"
        ),
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_weekday"),
        CheckConstraint("start_time < end_time", name="ck_working_hours_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    practitioner_id: Mapped[int] = mapped_column(
        ForeignKey("practitioners.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    practitioner: Mapped[Practitioner] = relationship(
        back_populates="working_hours"
    )


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'in_progress', 'completed', "
            "'cancelled', 'no_show')",
            name="ck_appointment_status",
        ),
        CheckConstraint("start_at < end_at", name="ck_appointment_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False
    )
    practitioner_id: Mapped[int] = mapped_column(
        ForeignKey("practitioners.id", ondelete="RESTRICT"), nullable=False
    )
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="scheduled", nullable=False
    )
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    patient: Mapped[Patient] = relationship(back_populates="appointments")
    practitioner: Mapped[Practitioner] = relationship(
        back_populates="appointments"
    )
    creator: Mapped[User] = relationship(
        back_populates="created_appointments",
        foreign_keys=[created_by_user_id],
    )
    queue_entries: Mapped[list[QueueEntry]] = relationship(
        back_populates="appointment"
    )


class QueueEntry(Base):
    __tablename__ = "queue_entries"
    __table_args__ = (
        UniqueConstraint(
            "practitioner_id", "queue_date", "position",
            name="uq_queue_position",
        ),
        CheckConstraint("position >= 1", name="ck_queue_position"),
        CheckConstraint(
            "status IN ('waiting', 'in_service', 'completed', 'cancelled')",
            name="ck_queue_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    practitioner_id: Mapped[int] = mapped_column(
        ForeignKey("practitioners.id", ondelete="RESTRICT"), nullable=False
    )
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False
    )
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True
    )
    queue_date: Mapped[date] = mapped_column(Date, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="waiting", nullable=False
    )

    practitioner: Mapped[Practitioner] = relationship(
        back_populates="queue_entries"
    )
    patient: Mapped[Patient] = relationship(back_populates="queue_entries")
    appointment: Mapped[Appointment | None] = relationship(
        back_populates="queue_entries"
    )
