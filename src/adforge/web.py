"""FastAPI desktop control plane for the single AdForge owner."""

import json
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
from adforge.models import Campaign, CampaignState, TaskState, TruthReadiness, WorkerNode
from adforge.orchestrator import ActiveCampaignError, Orchestrator, TransitionError
from adforge.providers import ClaudeCodeProvider, CodexCLIProvider, ProviderRouter
from adforge.renderer import FFmpegRenderer
from adforge.services import Services
from adforge.storage import UnsafePathError
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
                CampaignState.REPAIR: build_repair_handler(services, self.renderer),
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
    imports = (import_root or root / "imports").resolve()
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

    @app.get("/products/{product_id}", response_class=HTMLResponse)
    def product_detail(request: Request, product_id: str, _: Session) -> HTMLResponse:
        product = context.services.products.get(product_id)
        if product is None:
            raise HTTPException(status_code=404)
        return render(request, "product_detail.html", product=product)

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
    ) -> Response:
        require_csrf(session, csrf)
        if any(item.active for item in context.services.campaigns.list()):
            raise HTTPException(status_code=409, detail="one campaign is already active")
        product = context.services.products.get(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="product not found")
        validated_apk = validate_import_path(context.import_root, apk_path) if apk_path else None
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
        return render(request, "campaign_detail.html", campaign=campaign, tasks=tasks)

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
        context.orchestrator.create_task(
            task.campaign_id,
            task.task_type,
            f"{task.idempotency_key}:manual-retry:{task.attempt + 1}",
            dependencies=task.dependencies,
            targeted_asset_ids=task.targeted_asset_ids,
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


def secrets_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)
