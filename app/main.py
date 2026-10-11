from fastapi import FastAPI

app = FastAPI(
    title="CareSlot API",
    description="Backend API for clinic appointment management",
    version="0.1.0",
)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok", "service": "careslot-api"}


@app.get("/", tags=["Root"])
def root():
    return {"message": "Welcome to CareSlot API"}
