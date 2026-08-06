import gc
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from app.api import (
    auth_router,
    files_router,
    health_router,
    materials_router,
    parsed_router,
    review_router,
    runtime_settings_router,
    settings_router,
    upload_router,
    workflow_v2_router,
    workflow_v3_router,
)
from app.api import stats
from app.services.pipeline_recovery import recover_interrupted_pipeline_runs
from app.middleware.upload_envelope import UploadEnvelopeMiddleware
from app.workflow_v2 import initialize_workflow_database
from app.workflow_v3.database import initialize_workflow_v3_database
from contextlib import asynccontextmanager

def clean_memory():
    gc.collect()

@asynccontextmanager
async def life_span(app: FastAPI):
    app.state.predictor = None
    app.state.pipeline_recovery = recover_interrupted_pipeline_runs()
    app.state.workflow_v2 = initialize_workflow_database()
    app.state.workflow_v3 = initialize_workflow_v3_database()
    yield
    clean_memory()


app = FastAPI(title="MinerU 文档解析系统 API", lifespan=life_span)
app.add_middleware(UploadEnvelopeMiddleware)

# 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)



app.include_router(upload_router, prefix="/api", tags=["upload"])
app.include_router(auth_router, prefix="/api", tags=["auth"])
app.include_router(files_router, prefix="/api", tags=["files"])
app.include_router(materials_router, prefix="/api", tags=["materials"])
app.include_router(parsed_router, prefix="/api", tags=["parsed"])
app.include_router(review_router, prefix="/api", tags=["review"])
app.include_router(runtime_settings_router, prefix="/api", tags=["runtime"])
app.include_router(settings_router, prefix="/api", tags=["settings"])
app.include_router(health_router, prefix="/api", tags=["health"])
app.include_router(workflow_v2_router, prefix="/api", tags=["workflow-v2"])
app.include_router(workflow_v3_router, prefix="/api", tags=["workflow-v3"])
app.include_router(stats.router, prefix="/api", tags=["stats"])

@app.get("/ping")
def ping():
    return {"msg": "pong"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
