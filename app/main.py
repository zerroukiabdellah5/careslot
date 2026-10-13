from fastapi import FastAPI

from app.api.v1.appointments import router as appointments_router
from app.api.v1.auth import router as auth_router
from app.api.v1.patients import router as patients_router
from app.api.v1.queue import router as queue_router
from app.api.v1.working_hours import router as working_hours_router

app = FastAPI(
    title="CareSlot API",
    description="Backend API for clinic appointment management",
    version="0.2.0",
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(patients_router, prefix="/api/v1")
app.include_router(appointments_router, prefix="/api/v1")
app.include_router(working_hours_router, prefix="/api/v1")
app.include_router(queue_router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "service": "careslot-api"}


@app.get("/", tags=["Root"])
def root():
    return {"message": "Welcome to CareSlot API"}
