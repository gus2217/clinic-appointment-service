from fastapi import HTTPException

from app.api.deps import Principal
from app.models.appointment import Appointment


def _require(allowed: bool, detail: str) -> None:
    if not allowed:
        raise HTTPException(status_code=403, detail=detail)


def _is_owner_patient(principal: Principal, appointment: Appointment) -> bool:
    return principal.role == "patient" and principal.actor_id == str(appointment.patient_id)


def _is_owner_doctor(principal: Principal, appointment: Appointment) -> bool:
    return principal.role == "doctor" and principal.actor_id == str(appointment.doctor_id)


def can_view(principal: Principal, appointment: Appointment) -> None:
    _require(
        principal.role == "admin"
        or _is_owner_patient(principal, appointment)
        or _is_owner_doctor(principal, appointment),
        "You cannot view this appointment",
    )


def can_cancel(principal: Principal, appointment: Appointment) -> None:
    _require(
        principal.role == "admin"
        or _is_owner_patient(principal, appointment)
        or _is_owner_doctor(principal, appointment),
        "You cannot cancel this appointment",
    )


def can_reschedule(principal: Principal, appointment: Appointment) -> None:
    _require(
        principal.role == "admin" or _is_owner_patient(principal, appointment),
        "You cannot reschedule this appointment",
    )


def can_clinical(principal: Principal, appointment: Appointment) -> None:
    _require(
        principal.role == "admin" or _is_owner_doctor(principal, appointment),
        "Only the doctor or an admin can do this",
    )


def can_book(principal: Principal, patient_id: str) -> None:
    _require(
        principal.role == "admin"
        or (principal.role == "patient" and principal.actor_id == patient_id),
        "You can only book for yourself",
    )


def can_list_patient(principal: Principal, patient_id: str) -> None:
    _require(
        principal.role == "admin"
        or (principal.role == "patient" and principal.actor_id == patient_id),
        "You cannot view this patient's appointments",
    )
