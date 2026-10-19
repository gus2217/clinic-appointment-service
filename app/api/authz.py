from fastapi import HTTPException
from app.api.deps import Principal
from app.models.appointment import Appointment

def _forbidden(detail: str = "Forbidden") -> HTTPException:
    return HTTPException(status_code=403, detail=detail)

def _is_owner_patient(principal: Principal, appointment: Appointment) -> bool:
    return principal.role == "patient" and principal.actor_id == str(appointment.patient_id)

def _is_owner_doctor(principal: Principal, appointment: Appointment) -> bool:
    return principal.role == "doctor" and principal.actor_id == str(appointment.doctor_id)

def can_view(principal: Principal, appointment: Appointment) -> None:
    if principal.role == "admin" or _is_owner_patient(principal, appointment) or _is_owner_doctor(principal, appointment):
        return
    raise _forbidden("You cannot view this appointment")

def can_cancel(principal: Principal, appointment: Appointment) -> None:
    if principal.role == "admin" or _is_owner_patient(principal, appointment) or _is_owner_doctor(principal, appointment):
        return
    raise _forbidden("You cannot cancel this appointment")

def can_reschedule(principal: Principal, appointment: Appointment) -> None:
    if principal.role == "admin" or _is_owner_patient(principal, appointment):
        return
    raise _forbidden("You cannot reschedule this appointment")

def can_clinical(principal: Principal, appointment: Appointment) -> None:
    if principal.role == "admin" or _is_owner_doctor(principal, appointment):
        return
    raise _forbidden("Only the doctor or an admin can do this")

def can_book(principal: Principal, patient_id: str) -> None:
    if principal.role == "admin":
        return
    if principal.role == "patient" and principal.actor_id == patient_id:
        return
    raise _forbidden("You can only book for yourself")

def can_list_patient(principal: Principal, patient_id: str) -> None:
    if principal.role == "admin":
        return
    if principal.role == "patient" and principal.actor_id == patient_id:
        return
    raise _forbidden("You cannot view this patient's appointments")
