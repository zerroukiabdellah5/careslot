from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import (
    get_current_practitioner,
    get_current_user,
    get_db,
    get_practitioner_for_user,
)
from app.db.models import Patient, Practitioner, User, doctor_patients
from app.schemas.patient import PatientCreate, PatientResponse, PatientUpdate

router = APIRouter(prefix="/patients", tags=["Patients"])


def _to_response(patient: Patient) -> PatientResponse:
    return PatientResponse(
        id=patient.id,
        full_name=patient.full_name,
        phone=patient.phone,
        created_at=patient.created_at,
        practitioner_ids=sorted(p.id for p in patient.practitioners),
    )


def _get_patient(db: Session, patient_id: int) -> Patient:
    patient = (
        db.execute(
            select(Patient)
            .options(selectinload(Patient.practitioners))
            .where(Patient.id == patient_id, Patient.is_deleted.is_(False))
        )
        .scalar_one_or_none()
    )
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found",
        )
    return patient


def _authorize_patient_access(db: Session, user: User, patient: Patient) -> None:
    if user.role == "doctor":
        practitioner = get_practitioner_for_user(db, user)
        if practitioner.id not in {p.id for p in patient.practitioners}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this patient",
            )


@router.get("", response_model=list[PatientResponse])
def list_patients(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PatientResponse]:
    stmt = (
        select(Patient)
        .options(selectinload(Patient.practitioners))
        .where(Patient.is_deleted.is_(False))
        .order_by(Patient.id)
    )

    if current_user.role == "doctor":
        practitioner = get_practitioner_for_user(db, current_user)
        stmt = stmt.join(
            doctor_patients,
            doctor_patients.c.patient_id == Patient.id,
        ).where(doctor_patients.c.practitioner_id == practitioner.id)

    patients = db.scalars(stmt).all()
    return [_to_response(patient) for patient in patients]


@router.post("", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def create_patient(
    payload: PatientCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientResponse:
    patient = Patient(
        full_name=payload.full_name.strip(),
        phone=payload.phone.strip() if payload.phone else None,
        created_by_user_id=current_user.id,
    )
    db.add(patient)

    if current_user.role == "doctor":
        practitioner = get_practitioner_for_user(db, current_user)
        patient.practitioners.append(practitioner)

    db.commit()
    return _to_response(_get_patient(db, patient.id))


@router.get("/{patient_id}", response_model=PatientResponse)
def get_patient(
    patient_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientResponse:
    patient = _get_patient(db, patient_id)
    _authorize_patient_access(db, current_user, patient)
    return _to_response(patient)


@router.patch("/{patient_id}", response_model=PatientResponse)
def update_patient(
    patient_id: int,
    payload: PatientUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientResponse:
    patient = _get_patient(db, patient_id)
    _authorize_patient_access(db, current_user, patient)

    if payload.full_name is not None:
        patient.full_name = payload.full_name.strip()
    if payload.phone is not None:
        patient.phone = payload.phone.strip() if payload.phone else None

    db.commit()
    return _to_response(patient)


@router.delete("/{patient_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_patient(
    patient_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    patient = _get_patient(db, patient_id)

    if current_user.role == "doctor":
        practitioner = get_practitioner_for_user(db, current_user)
        if practitioner.id not in {p.id for p in patient.practitioners}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this patient",
            )
        db.execute(
            delete(doctor_patients).where(
                doctor_patients.c.practitioner_id == practitioner.id,
                doctor_patients.c.patient_id == patient.id,
            )
        )
        db.commit()
        return

    patient.is_deleted = True
    db.commit()


@router.post("/{patient_id}/associate", response_model=PatientResponse)
def associate_patient(
    patient_id: int,
    practitioner: Practitioner = Depends(get_current_practitioner),
    db: Session = Depends(get_db),
) -> PatientResponse:
    patient = _get_patient(db, patient_id)

    if any(p.id == practitioner.id for p in patient.practitioners):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Patient is already associated with this doctor",
        )

    patient.practitioners.append(practitioner)
    db.commit()
    return _to_response(_get_patient(db, patient.id))


@router.delete("/{patient_id}/associate", status_code=status.HTTP_204_NO_CONTENT)
def disassociate_patient(
    patient_id: int,
    practitioner: Practitioner = Depends(get_current_practitioner),
    db: Session = Depends(get_db),
) -> None:
    patient = _get_patient(db, patient_id)

    if not any(p.id == practitioner.id for p in patient.practitioners):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient is not associated with this doctor",
        )

    db.execute(
        delete(doctor_patients).where(
            doctor_patients.c.practitioner_id == practitioner.id,
            doctor_patients.c.patient_id == patient.id,
        )
    )
    db.commit()