"""FastAPI desktop control plane for the single AdForge owner."""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from adforge.android import APKIngestor, APKValidationError
from adforge.auth import SessionSigner, verify_password
from adforge.bootstrap import ensure_warranty_vault_product
from adforge.campaign_stages import (
    build_asset_plan_handler,
    build_audio_production_handler,
    build_draft_render_handler,
    build_edit_plan_handler,
    build_export_handler,
    build_final_render_handler,
    build_product_truth_validation_handler,
    build_qc_handler,
    build_repair_handler,
    build_script_handler,
    build_storyboard_handler,
    build_strategy_handler,
)
from adforge.health import collect_capabilities, platform_status
from adforge.models import (
    Campaign,
    CampaignState,
    Product,
    TaskState,
    TruthReadiness,
    WorkerJob,
    WorkerJobStatus,
    WorkerNode,
)
from adforge.orchestrator import ActiveCampaignError, Orchestrator, TransitionError
from adforge.product_truth import ProductTruthError, ProductTruthService
from adforge.providers import ClaudeCodeProvider, CodexCLIProvider, ProviderRouter
from adforge.renderer import FFmpegRenderer
from adforge.services import Services
from adforge.storage import UnsafePathError, safe_component
from adforge.worker import CampaignWorker
from adforge.worker_api import WorkerJobService, build_worker_router
from adforge.worker_auth import issue_token
from adforge.worker_stages import (
    WORKER_ARTIFACT_IMPORTERS,
    build_app_capture_handler,
    build_flow_generation_handler,
)

SESSION_COOKIE = "adforge_session"


class WebContext:
    def __init__(
        self,
        services: Services,
        signer: SessionSigner,
        password_hash: str,
        import_root: Path,
        templates: Jinja2Templates,
        secure_cookie: bool,
    ) -> None:
        self.services = services
        self.signer = signer
        self.password_hash = password_hash
        self.import_root = import_root.resolve()
        self.templates = templates
        self.secure_cookie = secure_cookie
        self.orchestrator = Orchestrator(services)
        self.worker_jobs = WorkerJobService(services)
        provider_workspace = services.storage.root / "temp" / "provider-workspace"
        self.provider_router = ProviderRouter(
            [ClaudeCodeProvider(provider_workspace), CodexCLIProvider(provider_workspace)]
        )
        self.renderer = FFmpegRenderer()
        self.campaign_worker = CampaignWorker(
            services,
            {
                CampaignState.PRODUCT_TRUTH_VALIDATION: build_product_truth_validation_handler(
                    services
                ),
                CampaignState.STRATEGY: build_strategy_handler(services, self.provider_router),
                CampaignState.SCRIPT: build_script_handler(services, self.provider_router),
                CampaignState.STORYBOARD: build_storyboard_handler(services, self.provider_router),
                CampaignState.ASSET_PLAN: build_asset_plan_handler(services, self.provider_router),
                CampaignState.ASSET_GENERATION: build_flow_generation_handler(
                    services, self.worker_jobs
                ),
                CampaignState.APP_CAPTURE: build_app_capture_handler(services, self.worker_jobs),
                CampaignState.AUDIO_PRODUCTION: build_audio_production_handler(services),
                CampaignState.EDIT_PLAN: build_edit_plan_handler(services, self.renderer),
                CampaignState.DRAFT_RENDER: build_draft_render_handler(services, self.renderer),
                CampaignState.QC: build_qc_handler(services, self.renderer),
                CampaignState.REPAIR: build_repair_handler(
                    services, self.provider_router, self.renderer, self.worker_jobs
                ),
                CampaignState.FINAL_RENDER: build_final_render_handler(services, self.renderer),
                CampaignState.EXPORT: build_export_handler(services),
            },
        )
        self.worker_jobs.artifact_importers = WORKER_ARTIFACT_IMPORTERS
        self.worker_jobs.on_campaign_resumed = self.campaign_worker.run


def create_app(
    *,
    runtime_root: Path | None = None,
    schema_root: Path | None = None,
    secret_key: str | None = None,
    password_hash: str | None = None,
    import_root: Path | None = None,
    secure_cookie: bool | None = None,
) -> FastAPI:
    package_root = Path(__file__).parent
    root = (runtime_root or Path(os.getenv("ADFORGE_DATA_ROOT", ".adforge-runtime"))).resolve()
    schemas = (schema_root or Path("schemas")).resolve()
    imports = (
        import_root or Path(os.getenv("ADFORGE_IMPORT_ROOT", str(root / "imports")))
    ).resolve()
    imports.mkdir(parents=True, exist_ok=True, mode=0o700)
    services = Services(root, schemas)
    services.initialize()
    ensure_warranty_vault_product(services)
    configured_secret = (
        secret_key if secret_key is not None else os.getenv("ADFORGE_SECRET_KEY", "")
    )
    signer = SessionSigner(configured_secret)
    configured_password = password_hash or os.getenv("ADFORGE_ADMIN_PASSWORD_HASH", "")
    if not configured_password:
        raise ValueError("ADFORGE_ADMIN_PASSWORD_HASH is required")
    templates = Jinja2Templates(directory=package_root / "templates")
    context = WebContext(
        services,
        signer,
        configured_password,
        imports,
        templates,
        secure_cookie
        if secure_cookie is not None
        else os.getenv("ADFORGE_SESSION_HTTPS_ONLY", "true").lower() == "true",
    )
    app = FastAPI(title="AdForge", docs_url=None, redoc_url=None)
    app.state.context = context
    app.mount("/static", StaticFiles(directory=package_root / "static"), name="static")
    context.worker_jobs.reclaim_expired()
    context.worker_jobs.sweep_offline()
    app.state.worker_services = services
    app.include_router(build_worker_router(services, context.worker_jobs))

    def current_session(request: Request) -> dict[str, Any]:
        session = context.signer.verify(request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return session

    Session = Annotated[dict[str, Any], Depends(current_session)]

    def render(request: Request, template: str, **values: Any) -> HTMLResponse:
        session = context.signer.verify(request.cookies.get(SESSION_COOKIE))
        return context.templates.TemplateResponse(
            request=request,
            name=template,
            context={"csrf": session.get("csrf") if session else None, **values},
        )

    def require_csrf(session: dict[str, Any], csrf: str) -> None:
        if not secrets_compare(str(session.get("csrf", "")), csrf):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")

    @app.exception_handler(status.HTTP_401_UNAUTHORIZED)
    async def unauthorized(request: Request, exc: HTTPException) -> Response:
        if request.url.path.startswith("/api/worker"):
            return Response(
                content=json.dumps({"detail": exc.detail}),
                status_code=status.HTTP_401_UNAUTHORIZED,
                media_type="application/json",
            )
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> HTMLResponse:
        return render(request, "login.html", error=None)

    @app.post("/login", response_class=HTMLResponse)
    def login(
        request: Request, password: Annotated[str, Form()]
    ) -> Response:
        if not verify_password(password, context.password_hash):
            return render(request, "login.html", error="Invalid credentials")
        token, _ = context.signer.create()
        response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            secure=context.secure_cookie,
            samesite="strict",
            max_age=context.signer.max_age_seconds,
        )
        return response

    @app.post("/logout")
    def logout(session: Session, csrf: Annotated[str, Form()]) -> RedirectResponse:
        require_csrf(session, csrf)
        response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, _: Session) -> HTMLResponse:
        campaigns = context.services.campaigns.list()
        return render(
            request,
            "dashboard.html",
            products=context.services.products.list(),
            campaigns=campaigns,
            active=next((item for item in campaigns if item.active), None),
        )

    @app.get("/products", response_class=HTMLResponse)
    def products(request: Request, _: Session) -> HTMLResponse:
        return render(request, "products.html", products=context.services.products.list())

    @app.get("/products/new", response_class=HTMLResponse)
    def new_product(request: Request, _: Session) -> HTMLResponse:
        return render(request, "new_product.html", error=None)

    @app.get("/products/{product_id}", response_class=HTMLResponse)
    def product_detail(request: Request, product_id: str, _: Session) -> HTMLResponse:
        product = context.services.products.get(product_id)
        if product is None:
            raise HTTPException(status_code=404)
        return render(request, "product_detail.html", product=product)

    @app.post("/products", response_class=HTMLResponse)
    def create_product(
        request: Request,
        session: Session,
        csrf: Annotated[str, Form()],
        name: Annotated[str, Form(min_length=1, max_length=100)],
        slug: Annotated[str, Form(min_length=1, max_length=100)],
        product_truth_json: Annotated[str, Form(min_length=1)],
    ) -> Response:
        require_csrf(session, csrf)
        try:
            truth = json.loads(product_truth_json)
        except json.JSONDecodeError as exc:
            return render(request, "new_product.html", error=f"invalid JSON: {exc}")
        try:
            product = Product.model_validate({"id": slug, "name": name, "slug": slug})
        except ValueError as exc:
            return render(request, "new_product.html", error=str(exc))
        runtime_product_dir = context.services.storage.root / "products" / slug / "truth"
        runtime_product_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        truth_path = runtime_product_dir / "PRODUCT_TRUTH.json"
        truth_path.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n")
        truth_service = ProductTruthService(context.services)
        saved = context.services.products.save(product)
        try:
            truth_service.import_for_product(saved, truth_path)
        except ProductTruthError as exc:
            return render(request, "new_product.html", error=str(exc))
        return RedirectResponse(f"/products/{slug}", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/campaigns/new", response_class=HTMLResponse)
    def new_campaign(request: Request, _: Session) -> HTMLResponse:
        active = next((item for item in context.services.campaigns.list() if item.active), None)
        return render(
            request,
            "new_campaign.html",
            products=context.services.products.list(),
            active=active,
            error=None,
        )

    @app.post("/campaigns", response_class=HTMLResponse)
    def create_campaign(
        request: Request,
        session: Session,
        csrf: Annotated[str, Form()],
        product_id: Annotated[str, Form()],
        name: Annotated[str, Form(min_length=1, max_length=100)],
        brief: Annotated[str, Form(min_length=1, max_length=10_000)],
        apk_path: Annotated[str, Form()] = "",
        apk_file: Annotated[UploadFile | None, File()] = None,
    ) -> Response:
        require_csrf(session, csrf)
        if any(item.active for item in context.services.campaigns.list()):
            raise HTTPException(status_code=409, detail="one campaign is already active")
        product = context.services.products.get(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="product not found")
        if apk_file is not None and apk_file.filename:
            validated_apk = stage_uploaded_apk(context.import_root, apk_file)
        elif apk_path:
            validated_apk = validate_import_path(context.import_root, apk_path)
        else:
            validated_apk = None
        campaign = context.services.campaigns.save(
            Campaign(product_id=product.id, name=name, brief=brief)
        )
        workspace = context.services.storage.campaign_workspace(campaign.id)
        (workspace / "brief" / "campaign.json").write_text(
            json.dumps(
                {"brief": brief, "apk_path": str(validated_apk) if validated_apk else None},
                indent=2,
            )
            + "\n"
        )
        if validated_apk is not None:
            try:
                APKIngestor(context.services, context.import_root).ingest(
                    campaign.id, validated_apk
                )
            except APKValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return RedirectResponse(
            f"/campaigns/{campaign.id}", status_code=status.HTTP_303_SEE_OTHER
        )

    @app.get("/campaigns", response_class=HTMLResponse)
    def campaign_queue(request: Request, _: Session) -> HTMLResponse:
        return render(
            request, "campaign_queue.html", campaigns=context.services.campaigns.list()
        )

    @app.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
    def campaign_detail(request: Request, campaign_id: str, _: Session) -> HTMLResponse:
        campaign = context.services.campaigns.get(campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404)
        tasks = context.services.tasks.find_by("campaign_id", campaign.id)
        open_jobs = [
            job
            for job in context.services.worker_jobs.find_by("campaign_id", campaign.id)
            if job.status
            in {WorkerJobStatus.PENDING, WorkerJobStatus.CLAIMED, WorkerJobStatus.FAILED}
        ]
        return render(
            request, "campaign_detail.html", campaign=campaign, tasks=tasks, open_jobs=open_jobs
        )

    @app.post("/worker-jobs/{job_id}/manual-complete")
    def manual_complete_worker_job(
        job_id: str,
        session: Session,
        csrf: Annotated[str, Form()],
        files: Annotated[list[UploadFile], File()],
    ) -> RedirectResponse:
        require_csrf(session, csrf)
        job = context.services.worker_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404)
        worker = manual_admin_worker(context.services)
        if job.status in {WorkerJobStatus.PENDING, WorkerJobStatus.FAILED}:
            job = context.worker_jobs.claim_specific(worker, job_id)
        elif job.worker_id != worker.id:
            raise HTTPException(
                status_code=409, detail="job is leased to a different worker"
            )
        for upload in files:
            content = upload.file.read()
            if not content:
                raise HTTPException(status_code=422, detail=f"{upload.filename} is empty")
            filename = resolve_manual_artifact_filename(job, upload.filename, len(files))
            context.worker_jobs.store_artifact(
                worker,
                job_id,
                filename=filename,
                content=content,
                content_type=upload.content_type or "application/octet-stream",
                declared_checksum=hashlib.sha256(content).hexdigest(),
            )
        context.worker_jobs.complete(worker, job_id)
        return RedirectResponse(
            f"/campaigns/{job.campaign_id}", status_code=status.HTTP_303_SEE_OTHER
        )

    @app.post("/campaigns/{campaign_id}/start")
    def start_campaign(
        campaign_id: str, session: Session, csrf: Annotated[str, Form()]
    ) -> RedirectResponse:
        require_csrf(session, csrf)
        campaign = context.services.campaigns.get(campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404)
        product = context.services.products.get(campaign.product_id)
        if product is None or product.truth_readiness != TruthReadiness.READY:
            raise HTTPException(
                status_code=409,
                detail="campaign cannot start until Product Truth is READY",
            )
        try:
            context.orchestrator.transition(
                campaign_id, CampaignState.PRODUCT_TRUTH_VALIDATION
            )
        except ActiveCampaignError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        context.campaign_worker.run(campaign_id)
        return RedirectResponse(
            f"/campaigns/{campaign_id}", status_code=status.HTTP_303_SEE_OTHER
        )

    @app.post("/campaigns/{campaign_id}/resume")
    def resume_campaign(
        campaign_id: str, session: Session, csrf: Annotated[str, Form()]
    ) -> RedirectResponse:
        require_csrf(session, csrf)
        try:
            context.orchestrator.resume(campaign_id)
        except (TransitionError, ActiveCampaignError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        context.campaign_worker.run(campaign_id)
        return RedirectResponse(
            f"/campaigns/{campaign_id}", status_code=status.HTTP_303_SEE_OTHER
        )

    @app.post("/tasks/{task_id}/retry")
    def retry_task(
        task_id: str, session: Session, csrf: Annotated[str, Form()]
    ) -> RedirectResponse:
        require_csrf(session, csrf)
        task = context.services.tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404)
        if task.state not in {TaskState.BLOCKED, TaskState.FAILED}:
            raise HTTPException(status_code=409, detail="task is not retryable")
        # Reset the existing task in place (attempt/state only) rather than
        # creating a new CampaignTask row under a "...:manual-retry:N" key.
        # CampaignWorker.run() only ever looks up (and creates, if missing) the
        # task at its own deterministic idempotency key
        # (f"stage:{state}:v{transition_count}") -- a differently-keyed row is
        # simply never discovered by it, so the previous create_task() call left
        # every CampaignWorker-driven stage's "Retry" button doing nothing
        # (verified live against the real DemoTask campaign). This also matches
        # how Orchestrator.execute_task's own internal retry loop already works:
        # it re-saves the same task row per attempt, not a new row -- history is
        # the ledger's job (task_attempt_started/failed events), not extra rows.
        context.services.tasks.save(
            task.model_copy(
                update={"attempt": 0, "state": TaskState.PENDING, "failure_summary": None}
            )
        )
        return RedirectResponse(
            f"/campaigns/{task.campaign_id}", status_code=status.HTTP_303_SEE_OTHER
        )

    @app.get("/campaigns/{campaign_id}/assets", response_class=HTMLResponse)
    def assets(request: Request, campaign_id: str, _: Session) -> HTMLResponse:
        return render(
            request,
            "assets.html",
            campaign_id=campaign_id,
            assets=context.services.assets.find_by("campaign_id", campaign_id),
        )

    @app.get("/campaigns/{campaign_id}/ledger", response_class=HTMLResponse)
    def ledger(request: Request, campaign_id: str, _: Session) -> HTMLResponse:
        return render(
            request,
            "ledger.html",
            campaign_id=campaign_id,
            events=context.services.ledger.read(campaign_id),
        )

    @app.get("/outputs", response_class=HTMLResponse)
    def outputs(request: Request, _: Session) -> HTMLResponse:
        return render(request, "outputs.html", renders=context.services.renders.list())

    @app.get("/outputs/{render_id}/download")
    def download(render_id: str, _: Session) -> FileResponse:
        render_record = context.services.renders.get(render_id)
        if render_record is None or render_record.status != "COMPLETE":
            raise HTTPException(status_code=404)
        try:
            path = context.services.storage.campaign_path(
                render_record.campaign_id, *Path(render_record.output_path).parts
            )
        except UnsafePathError as exc:
            raise HTTPException(status_code=400, detail="unsafe output path") from exc
        if not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(path, filename=path.name, media_type="video/mp4")

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, _: Session) -> HTMLResponse:
        capabilities = collect_capabilities(context.services)
        return render(
            request,
            "settings.html",
            capabilities=capabilities,
            platform=platform_status(capabilities),
        )

    @app.post("/settings/diagnostics/run")
    def run_diagnostics(
        session: Session, csrf: Annotated[str, Form()]
    ) -> RedirectResponse:
        require_csrf(session, csrf)
        collect_capabilities(context.services, force_slow=True)
        return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/settings/workers", response_class=HTMLResponse)
    def workers(request: Request, _: Session) -> HTMLResponse:
        context.worker_jobs.sweep_offline()
        return render(
            request, "workers.html", workers=context.services.worker_nodes.list(), new_token=None
        )

    @app.post("/settings/workers", response_class=HTMLResponse)
    def create_worker(
        request: Request,
        session: Session,
        csrf: Annotated[str, Form()],
        name: Annotated[str, Form(min_length=1, max_length=100)],
        capabilities: Annotated[str, Form()] = "synthetic_echo",
    ) -> HTMLResponse:
        require_csrf(session, csrf)
        worker = context.services.worker_nodes.save(
            WorkerNode(
                name=name,
                agent_version="unregistered",
                os="unknown",
                architecture="unknown",
                capabilities=[item.strip() for item in capabilities.split(",") if item.strip()],
            )
        )
        raw_token = issue_token(context.services, worker)
        return render(
            request,
            "workers.html",
            workers=context.services.worker_nodes.list(),
            new_token=raw_token,
            new_worker_name=worker.name,
        )

    @app.get("/settings/workers/{worker_id}", response_class=HTMLResponse)
    def worker_detail(request: Request, worker_id: str, _: Session) -> HTMLResponse:
        worker = context.services.worker_nodes.get(worker_id)
        if worker is None:
            raise HTTPException(status_code=404)
        jobs = context.services.worker_jobs.find_by("worker_id", worker_id)
        attempts = [
            attempt
            for attempt in context.services.worker_job_attempts.list()
            if attempt.worker_id == worker_id
        ]
        return render(
            request, "worker_detail.html", worker=worker, jobs=jobs, attempts=attempts
        )

    @app.post("/settings/workers/{worker_id}/revoke")
    def revoke_worker(
        worker_id: str, session: Session, csrf: Annotated[str, Form()]
    ) -> RedirectResponse:
        require_csrf(session, csrf)
        worker = context.services.worker_nodes.get(worker_id)
        if worker is None:
            raise HTTPException(status_code=404)
        for token in context.services.worker_tokens.find_by("worker_id", worker_id):
            if not token.revoked:
                context.services.worker_tokens.save(token.model_copy(update={"revoked": True}))
        return RedirectResponse("/settings/workers", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/settings/workers/{worker_id}/rotate", response_class=HTMLResponse)
    def rotate_worker_token(
        request: Request, worker_id: str, session: Session, csrf: Annotated[str, Form()]
    ) -> HTMLResponse:
        require_csrf(session, csrf)
        worker = context.services.worker_nodes.get(worker_id)
        if worker is None:
            raise HTTPException(status_code=404)
        raw_token = issue_token(context.services, worker)
        return render(
            request,
            "workers.html",
            workers=context.services.worker_nodes.list(),
            new_token=raw_token,
            new_worker_name=worker.name,
        )

    return app


def validate_import_path(import_root: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        raise HTTPException(status_code=422, detail="APK path must be absolute")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(import_root.resolve()):
        raise HTTPException(status_code=422, detail="APK path is outside the import root")
    if resolved.suffix.lower() != ".apk" or not resolved.is_file():
        raise HTTPException(status_code=422, detail="APK path must name an existing .apk file")
    return resolved


def stage_uploaded_apk(import_root: Path, upload: UploadFile) -> Path:
    """Save a real browser-uploaded APK into the import root under a generated,
    collision-resistant name (the campaign doesn't exist yet at this point in
    `create_campaign`, so it can't be namespaced by campaign id, and the
    original filename is discarded rather than sanitized -- it isn't kept
    anywhere; `APKIngestor.ingest()` copies it into the campaign workspace as
    `source.apk` regardless). Returns the path exactly as `validate_import_path`
    would for an operator-placed file -- the two converge on the same
    `APKIngestor.ingest()` call afterward.
    """
    if Path(upload.filename or "").suffix.lower() != ".apk":
        raise HTTPException(status_code=422, detail="uploaded file must be a .apk file")
    destination = (import_root / f"{uuid4().hex}.apk").resolve()
    content = upload.file.read()
    if not content:
        raise HTTPException(status_code=422, detail="uploaded APK is empty")
    destination.write_bytes(content)
    return destination


def secrets_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


MANUAL_WORKER_ID = "manual-admin-worker"


def manual_admin_worker(services: Services) -> WorkerNode:
    """The synthetic "worker" identity for a human operator manually fulfilling a
    WorkerJob through the web UI (uploading a file they generated themselves --
    e.g. a Flow video pasted/generated by hand when automated generation isn't
    available) rather than a real distributed worker completing it. Reuses the
    exact same claim/store_artifact/complete path a real worker uses, so a
    manually-completed job triggers identical artifact import and campaign
    auto-resume.
    """
    existing = services.worker_nodes.get(MANUAL_WORKER_ID)
    if existing is not None:
        return existing
    return services.worker_nodes.save(
        WorkerNode(
            id=MANUAL_WORKER_ID,
            name="Manual (web UI)",
            agent_version="manual",
            os="manual",
            architecture="manual",
            capabilities=[],
        )
    )


def resolve_manual_artifact_filename(
    job: WorkerJob, original: str | None, upload_count: int
) -> str:
    """Turn a browser-supplied upload filename into one `store_artifact`'s
    `safe_component()` will accept.

    Found live: a real Flow-downloaded video's filename (spaces, parentheses --
    ordinary browser download naming) doesn't satisfy `safe_component`'s strict
    pattern, and `store_artifact` raised that `UnsafePathError` straight into an
    uncaught 500. When the job expects exactly one output file and exactly one
    file was uploaded (the common case: `flow_generation`), use the job's own
    already-safe expected filename directly instead of trusting the browser's
    name at all -- it's what the artifact importer looks for regardless. For
    anything else, sanitize; fall back to a generated name if nothing usable
    survives.
    """
    expected = job.payload.get("output_filename")
    if upload_count == 1 and isinstance(expected, str) and expected:
        return expected
    candidate = Path(original or "").name
    try:
        return safe_component(candidate)
    except UnsafePathError:
        pass
    suffix = Path(candidate).suffix.lower().lstrip(".")
    safe_suffix = suffix if re.fullmatch(r"[a-z0-9]{1,10}", suffix) else "bin"
    return f"upload-{uuid4().hex}.{safe_suffix}"
