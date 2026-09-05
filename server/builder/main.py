import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .boot import run_startup_checks
from .routes import auth_routes, privacy, push, sessions, social, sync, users
from .settings import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("builder")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # These raise SystemExit rather than logging a warning. The failure they catch —
    # connecting as a role that bypasses row level security — is invisible at runtime and
    # makes every isolation test pass while enforcing nothing, so crashing on boot is the
    # only response loud enough to be safe.
    run_startup_checks()
    log.info("builder api up (%s)", settings().environment)
    yield


app = FastAPI(title="Builder", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings().base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router)
app.include_router(sync.router)
app.include_router(sessions.router)
app.include_router(push.router)
app.include_router(privacy.router)
# users BEFORE social: `/v1/users/me` must be registered ahead of `/v1/users/{handle}`,
# or the literal "me" is handed to the profile route as a handle.
app.include_router(users.router)
app.include_router(social.router)


@app.get("/health")
def health():
    return {"status": "ok", "environment": settings().environment}
