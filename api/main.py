import datetime, json, logging, os, time, uuid, uvicorn, asyncpg
from typing import Any
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, Query, Request, status, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from shared.extraction import extract_clinical_data, compute_bmi
from shared.models import (
    ManualPatientIntake,
    ManualIntakeResponse,
    PatientMatchResponse,
    PatientSnapshot,
    ObservationHistoryEntry,
    PatientStats,
    BmiDistribution,
    RecentActivityEntry,
)
from shared.queries import (
    UPSERT_QUERY,
    OBSERVATION_UPSERT_QUERY,
    FIND_PATIENT_BY_NAME_DOB_QUERY,
    MANUAL_ENTRY_UPSERT_QUERY,
    OBSERVATIONS_BY_PATIENT_QUERY,
    PATIENT_BY_ID_QUERY,
    PATIENT_STATS_QUERY,
    RECENT_ACTIVITY_QUERY,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("clinical_api")

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
# Comma-separated list of allowed frontend origins. Defaults to the local
# Streamlit dashboard's own origin -- deliberately not "*", since a wildcard
# lets literally any website's JS read this API's responses from a logged-in
# user's browser. Override via env var for a real deployed dashboard origin.
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:8501").split(",")
# Defaults to 1 -- the Supabase free-tier accommodation from earlier. Overridable
# via env var so a load-test experiment can compare pool sizes without editing
# and reverting this file between runs.
POOL_MAX_SIZE = int(os.getenv("POOL_MAX_SIZE", "1"))
# Required on every write endpoint (ingestion, manual entry). Read-only endpoints
# stay open since the dashboard hits them without a key. Fails closed if unset --
# a missing API_KEY is a misconfiguration, not "no auth needed".
API_KEY = os.getenv("API_KEY")

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=POOL_MAX_SIZE,
        statement_cache_size=0,
    )
    print(
        f"[startup] pool ready: min_size={app.state.pool.get_min_size()} "
        f"max_size={app.state.pool.get_max_size()} "
        f"(POOL_MAX_SIZE env={POOL_MAX_SIZE}) "
        f"host={DATABASE_URL.split('@')[-1].split('/')[0] if DATABASE_URL else None}"
    )
    yield
    await app.state.pool.close()

app = FastAPI(
    title="Clinical Data Platform API",
    version="1.0.0",
    lifespan=lifespan,
)

# Per-client-IP rate limiting on the write endpoints -- a basic defensive layer
# against a runaway client or naive abuse, independent of the API key check.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_request_id_and_timing(request: Request, call_next):
    """
    Tags every response with a short request id and logs method, path, status,
    and duration. Cheap to add, and it's the first thing you reach for once a
    load test (like the pooling investigation below) raises "which request was
    slow?" instead of just "the p99 was slow."
    """
    request_id = str(uuid.uuid4())[:8]
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        f"[{request_id}] {request.method} {request.url.path} "
        f"-> {response.status_code} ({duration_ms:.1f}ms)"
    )
    return response

async def get_db():
    async with app.state.pool.acquire() as conn:
        yield conn

async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    if not API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server is not configured with an API_KEY.",
        )
    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )

@app.get("/api/health")
async def health_check(conn: asyncpg.Connection = Depends(get_db)):
    row = await conn.fetchrow("SELECT 1 AS alive;")
    return {"status": "healthy", "database": row["alive"] == 1}

@app.get("/api/patients")
async def get_patients(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    gender: str | None = Query(None),
    bmi_category: str | None = Query(None),
    order_by: str = Query("birth_date", pattern="^(birth_date|created_at)$"),
    conn: asyncpg.Connection = Depends(get_db),
):
    conditions = []
    params: list[Any] = []

    if gender:
        params.append(gender.lower())
        conditions.append(f"gender = ${len(params)}")

    if bmi_category:
        params.append(bmi_category.title())
        conditions.append(f"bmi_category = ${len(params)}")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    params.append(limit)
    limit_clause = f"LIMIT ${len(params)}"

    params.append(offset)
    offset_clause = f"OFFSET ${len(params)}"

    order_column = {"birth_date": "birth_date", "created_at": "created_at"}[order_by]

    query = f"""
        SELECT 
            id, first_name, last_name, gender, birth_date, height_cm, weight_kg,
            bmi, bmi_category, latest_systolic_bp, latest_diastolic_bp, created_at
        FROM patients
        {where_clause}
        ORDER BY {order_column} DESC
        {limit_clause} {offset_clause};
    """

    rows = await conn.fetch(query, *params)
    return [dict(row) for row in rows]

@app.get("/api/stats", response_model=PatientStats)
async def get_patient_stats(conn: asyncpg.Connection = Depends(get_db)):
    """
    SQL-side aggregates for the dashboard's Home view. Deliberately not
    "fetch a page of patients and average them in Python" -- that undercounts
    the moment the table has more rows than the page size, and does needless
    work pulling full rows just to look at three numbers.
    """
    row = await conn.fetchrow(PATIENT_STATS_QUERY)
    return PatientStats(
        total_patients=row["total_patients"],
        avg_bmi=float(row["avg_bmi"]) if row["avg_bmi"] is not None else None,
        bmi_distribution=BmiDistribution(
            underweight=row["underweight_count"],
            normal=row["normal_count"],
            overweight=row["overweight_count"],
            obese=row["obese_count"],
        ),
        most_recent_patient_at=row["most_recent_patient_at"],
    )

@app.get("/api/patients/recent-activity", response_model=list[RecentActivityEntry])
async def get_recent_activity(
    limit: int = Query(5, ge=1, le=50),
    conn: asyncpg.Connection = Depends(get_db),
):
    rows = await conn.fetch(RECENT_ACTIVITY_QUERY, limit)
    return [RecentActivityEntry(**dict(row)) for row in rows]

@app.get("/api/patients/lookup", response_model=PatientMatchResponse)
async def lookup_patient_by_name_dob(
    first_name: str,
    last_name: str,
    birth_date: datetime.date,
    conn: asyncpg.Connection = Depends(get_db),
):
    existing = await conn.fetchrow(
        FIND_PATIENT_BY_NAME_DOB_QUERY,
        first_name.strip(),
        last_name.strip(),
        birth_date,
    )
    return PatientMatchResponse(
        match_found=existing is not None,
        patient=PatientSnapshot(**dict(existing)) if existing else None,
    )

@app.get("/api/patients/{patient_id}/observations", response_model=list[ObservationHistoryEntry])
async def get_patient_observations(
    patient_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_db),
):
    rows = await conn.fetch(OBSERVATIONS_BY_PATIENT_QUERY, patient_id)
    return [ObservationHistoryEntry(**dict(row)) for row in rows]

@app.get("/api/patients/{patient_id}", response_model=PatientSnapshot)
async def get_patient_by_id(
    patient_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_db),
):
    row = await conn.fetchrow(PATIENT_BY_ID_QUERY, patient_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No patient exists with that id.",
        )
    return PatientSnapshot(**dict(row))

@app.post(
    "/api/patients/ingest",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit("30/minute")
async def ingest_single_bundle(
    request: Request,
    payload: dict[str, Any],
    conn: asyncpg.Connection = Depends(get_db),
):
    parsed = extract_clinical_data(payload)
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Payload failed validation: missing Patient resource or invalid Bundle structure.",
        )

    patient = parsed["patient"]
    record_tuple = (
        uuid.UUID(str(patient["id"])),
        patient["gender"],
        patient["birth_date"],
        patient["height_cm"],
        patient["weight_kg"],
        patient["bmi"],
        patient["bmi_category"],
        patient["latest_systolic_bp"],
        patient["latest_diastolic_bp"],
        json.dumps(patient["raw_bundle"]) if patient["raw_bundle"] is not None else None,
        None,  # ingestion_run_id -- this endpoint is direct/manual ingestion, not
               # a tagged scheduled run, so COALESCE in UPSERT_QUERY leaves any
               # existing tag on the row untouched.
    )

    await conn.execute(UPSERT_QUERY, *record_tuple)
    return {
        "status": "upserted",
        "patient_id": str(patient["id"]),
        "bmi": patient["bmi"],
        "bmi_category": patient["bmi_category"],
    }

@app.post(
    "/api/patients/manual-entry",
    status_code=status.HTTP_201_CREATED,
    response_model=ManualIntakeResponse,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit("30/minute")
async def manual_patient_entry(
    request: Request,
    intake: ManualPatientIntake,
    conn: asyncpg.Connection = Depends(get_db),
):
    existing = None
    if not intake.force_new:
        existing = await conn.fetchrow(
            FIND_PATIENT_BY_NAME_DOB_QUERY,
            intake.first_name,
            intake.last_name,
            intake.birth_date,
        )
    matched_existing = existing is not None
    patient_id = existing["id"] if matched_existing else uuid.uuid4()

    bmi, bmi_category = compute_bmi(intake.height_cm, intake.weight_kg)

    obs_date = intake.observation_date or datetime.date.today()
    observation_rows = []
    if intake.height_cm is not None:
        observation_rows.append((uuid.uuid4(), patient_id, "8302-2", "Body Height", intake.height_cm, "cm", obs_date))
    if intake.weight_kg is not None:
        observation_rows.append((uuid.uuid4(), patient_id, "29463-7", "Body Weight", intake.weight_kg, "kg", obs_date))
    if intake.systolic_bp is not None:
        observation_rows.append((uuid.uuid4(), patient_id, "8480-6", "Systolic Blood Pressure", intake.systolic_bp, "mm[Hg]", obs_date))
    if intake.diastolic_bp is not None:
        observation_rows.append((uuid.uuid4(), patient_id, "8462-4", "Diastolic Blood Pressure", intake.diastolic_bp, "mm[Hg]", obs_date))

    # Same atomicity concern as the batch worker: a patient upsert and its
    # observation rows are one clinical fact (this visit), not two independent
    # writes. Wrapping them in a transaction means a crash mid-write leaves
    # neither behind, instead of a patient record with no vitals attached.
    async with conn.transaction():
        await conn.execute(
            MANUAL_ENTRY_UPSERT_QUERY,
            patient_id,
            intake.first_name,
            intake.last_name,
            intake.gender,
            intake.birth_date,
            intake.height_cm,
            intake.weight_kg,
            bmi,
            bmi_category,
            intake.systolic_bp,
            intake.diastolic_bp,
        )
        if observation_rows:
            await conn.executemany(OBSERVATION_UPSERT_QUERY, observation_rows)

    return ManualIntakeResponse(
        patient_id=patient_id,
        matched_existing_patient=matched_existing,
        bmi=bmi,
        bmi_category=bmi_category,
        observations_recorded=len(observation_rows),
    )
