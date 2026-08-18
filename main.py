from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from routers.auth import limiter
from routers.auth import router as auth_router

app = FastAPI(title="CropCare API")

# Attach SlowAPI rate-limiter state and exception handler
app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    _rate_limit_exceeded_handler,
)
app.add_middleware(SlowAPIMiddleware)

# Routers
app.include_router(auth_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}