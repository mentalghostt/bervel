"""Explicit HTTP contracts for tender imports, runs and evidence-backed reports."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated,Literal
from uuid import UUID
from fastapi import APIRouter,Depends
from pydantic import BaseModel,ConfigDict,Field
from fastapi.responses import Response
from bervel.core.errors import CoreError
from bervel.modules.tender_radar.infrastructure.pdf import render_radar_pdf
from bervel.core.models import ActorContext,ArtifactRef
from bervel.core.api.sources import SourceResponse

class Ref(BaseModel):
    model_config=ConfigDict(extra='forbid')
    artifact_id:UUID

class ImportBody(Ref):
    command_key:str=Field(min_length=1,max_length=128)

class RunBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    command_key:str=Field(min_length=1,max_length=128)
    batch_id:UUID
    sources:list[Ref]=Field(min_length=1,max_length=10)
    focus:str=Field(default='',max_length=1000)
    stock_source:Ref|None=None
    max_shortage_percent:Decimal=Field(default=Decimal('0'),ge=0,le=100,max_digits=5,decimal_places=2,allow_inf_nan=False)

class FiltersBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    min_price:Decimal|None=Field(default=None,ge=0,max_digits=30,decimal_places=12,allow_inf_nan=False)
    max_price:Decimal|None=Field(default=None,ge=0,max_digits=30,decimal_places=12,allow_inf_nan=False)
    min_days_remaining:int=Field(default=0,ge=0,le=3650,strict=True)
    focus:str=Field(default='',max_length=1000)
    max_shortage_percent:Decimal=Field(default=Decimal('0'),ge=0,le=100,max_digits=5,decimal_places=2,allow_inf_nan=False)
    min_margin_percent:Decimal=Field(default=Decimal('15'),ge=0,le=100,max_digits=5,decimal_places=2,allow_inf_nan=False)

class AnalyseBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    procurement_source:Ref
    stock_source:Ref
    economics_source:Ref
    filters:FiltersBody=Field(default_factory=FiltersBody)
    command_key:str=Field(min_length=1,max_length=128)

class BatchResponse(BaseModel):
    batch_id:UUID
    artifact_id:UUID
    filename:str
    created_at:datetime
    count:int
    warnings:list[str]
    skipped_rows:int
    duplicate_rows:int

class SnapshotResponse(Ref):
    filename:str
    media_type:str
    created_at:datetime
    size:int

class OriginResponse(BaseModel):
    sheet:str|None
    row:int

class SelectionResponse(BaseModel):
    stock_source:SnapshotResponse|None=None
    max_shortage_percent:str='0'
    procurement_source:SnapshotResponse|None=None
    economics_source:SnapshotResponse|None=None
    filters:FiltersBody|None=None
    pipeline_version:str|None=None

class PositionResponse(BaseModel):
    sku:str|None
    exact_match:bool
    quantity:str|None
    unit:str|None
    quote:str

class StockLineResponse(BaseModel):
    sku:str
    product:str
    unit:str
    required:str
    available:str
    shortage:str
    shortage_percent:str
    within_tolerance:bool
    source_row:int

class StockCheckResponse(BaseModel):
    status:Literal['pass','insufficient','unknown']
    allowed_shortage_percent:str
    checks:list[StockLineResponse]
    reasons:list[str]

class EconomicsLineResponse(BaseModel):
    sku:str
    quantity:str
    sale_price:str
    revenue:str
    cost:str
    source_row:int

class EconomicsCheckResponse(BaseModel):
    status:Literal['pass','fail','unknown']
    currency:str|None
    revenue:str|None
    total_cost:str|None
    profit:str|None
    margin_percent:str|None
    budget:str|None
    required_days:str|None
    available_days:str|None
    reasons:list[str]
    lines:list[EconomicsLineResponse]

class FunnelResponse(BaseModel):
    total:int
    after_eligibility:int
    after_names:int
    semantic_relevant:int
    quantity_passed:int
    model_requests:int=0

class RadarItemResponse(BaseModel):
    id:str
    number:str
    title:str
    source_url:str
    deadline_at:datetime|None
    source_observed_at:datetime|None
    price:str|None
    currency:str
    stage:Literal['open','closed','cancelled','unknown']
    demo:bool
    origin:OriginResponse|None
    provenance:str
    participation_ready:bool
    technical_readiness:Literal['unknown','ready']
    production_readiness:Literal['unknown','ready','insufficient']
    economics:Literal['unknown','pass','fail']
    risks:list[str]
    next_steps:list[str]
    evidence_indexes:list[int]
    decision:Literal['take_in_work','needs_review','excluded','not_analysed']
    reason:str
    fit:Literal['relevant','uncertain','irrelevant']|None=None
    product_group:str|None=None
    tender_quote:str|None=None
    profile_quote:str|None=None
    decision_stage:Literal['eligibility','names','meaning','quantity','economics']|None=None
    meaning_reason:str|None=None
    positions:list[PositionResponse]=Field(default_factory=list)
    positions_complete:bool=False
    stock_check:StockCheckResponse|None=None
    economics_check:EconomicsCheckResponse|None=None
    mock:bool=False
    origins:list[OriginResponse|None]=Field(default_factory=list)

class CoverageResponse(BaseModel):
    input_rows:int
    unique_tenders:int
    duplicate_rows:int
    skipped_rows:int
    accounted_rows:int
    model_failed_rows:int
    complete:bool

class PortionErrorResponse(BaseModel):
    ids:list[str]
    code:str
    message:str

class CountsResponse(BaseModel):
    total:int
    model_analysed:int
    take_in_work:int
    needs_review:int
    excluded:int
    not_analysed:int

class ReportBatchResponse(BaseModel):
    batch_id:UUID
    filename:str
    artifact_id:UUID

class ReportResponse(BaseModel):
    schema_id:Literal['bervel.tender-radar.report']
    schema_version:Literal['1.0.0','1.1.0','1.2.0','1.3.0']
    as_of:datetime
    counts:CountsResponse
    items:list[RadarItemResponse]
    sources:list[SourceResponse]
    notice:str
    batch:ReportBatchResponse
    selection:SelectionResponse|None=None
    stock_source:SnapshotResponse|None=None
    stock_kind:Literal['mock','reported']|None=None
    funnel:FunnelResponse|None=None
    economics_source:SnapshotResponse|None=None
    economics_kind:Literal['mock','reported']|None=None
    coverage:CoverageResponse|None=None
    portion_errors:list[PortionErrorResponse]=Field(default_factory=list)
    analysis_complete:bool|None=None

class RunSummaryResponse(BaseModel):
    run_id:UUID
    batch_id:UUID
    created_at:datetime
    focus:str
    state:Literal['queued','running','completed','failed']

class RunResponse(RunSummaryResponse):
    sources_snapshot:list[SnapshotResponse]
    selection:SelectionResponse=Field(default_factory=SelectionResponse)
    report:ReportResponse|None
    model:str|None
    error:str|None

def radar_router(service,actor):
    router=APIRouter(prefix='/api/v0.1/radar',tags=['Тендерный радар'])
    @router.post('/analyse',status_code=202,response_model=RunResponse)
    def analyse(body:AnalyseBody,context:Annotated[ActorContext,Depends(actor)]):
        refs=[ArtifactRef(r.artifact_id) for r in (body.procurement_source,body.stock_source,body.economics_source)]
        return service.analyse(context,*refs,body.filters.model_dump(),body.command_key)
    @router.post('/batches',response_model=BatchResponse)
    def import_batch(body:ImportBody,context:Annotated[ActorContext,Depends(actor)]):
        return service.import_batch(context,ArtifactRef(body.artifact_id),body.command_key)
    @router.get('/batches',response_model=list[BatchResponse])
    def list_batches(context:Annotated[ActorContext,Depends(actor)]): return service.list_batches(context)
    @router.post('/runs',status_code=202,response_model=RunResponse)
    def start(body:RunBody,context:Annotated[ActorContext,Depends(actor)]):
        return service.start(context,body.batch_id,[ArtifactRef(ref.artifact_id) for ref in body.sources],body.focus,body.command_key,
            stock_ref=ArtifactRef(body.stock_source.artifact_id) if body.stock_source else None,
            max_shortage_percent=body.max_shortage_percent)
    @router.get('/runs',response_model=list[RunSummaryResponse])
    def list_runs(context:Annotated[ActorContext,Depends(actor)]): return service.list_runs(context)
    @router.get('/runs/{run_id}',response_model=RunResponse)
    def read(run_id:UUID,context:Annotated[ActorContext,Depends(actor)]): return service.read(context,run_id)
    @router.get('/runs/{run_id}/report.pdf',response_class=Response)
    def pdf(run_id:UUID,context:Annotated[ActorContext,Depends(actor)]):
        run=service.read(context,run_id)
        if run['report'] is None:
            raise CoreError('VALIDATION_ERROR','Сохранённый отчёт пока недоступен.')
        return Response(render_radar_pdf(run),media_type='application/pdf',headers={
            'Content-Disposition':f'attachment; filename="bervel-radar-{run_id}.pdf"',
            'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})
    return router
