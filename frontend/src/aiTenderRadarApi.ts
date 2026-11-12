export type JobState = 'waiting' | 'queued' | 'running' | 'loaded' | 'no_match' | 'failed'
type AnalysisStatus = 'complete' | 'insufficient_data' | 'partial'
type ErrorInfo = {code: string; message: string}
type Tender = {id: string; title: string; number: string | null; link: string | null; position_count: number;
  limitations: {code: string; lot?: number; position_id?: string}[]}
type Economics = {revenue: string | null; cost: string | null; currency: string | null; profit: string | null;
  margin_percent: string | null; status: 'profitable' | 'loss' | 'break_even' | 'unknown'; reason: string | null;
  scope?: 'full' | 'confirmed' | null}
export type Position = {id: string; lot: number; customer: number | null; name: string | null; unit: string | null;
  required: string | null; covered: string; shortage: string | null;
  mass_conversion?: {weight_per_piece_kg: string; required_pieces: string};
  stock_status: 'full' | 'partial' | 'none' | 'unconfirmed'; stock_reason: string | null; economics: Economics;
  matches: {sku: string; stock_row: number; stock_unit: string; stock_free_quantity: string | null;
    evidence: {requirement: string; tender_value: string; stock_field: string; stock_value: string}[]}[];
  allocations: {sku: string; stock_quantity: string; stock_unit: string; stock_unit_cost: string | null;
    stock_currency: string | null; position_quantity: string}[]}
export type Job = {job_id: string; batch_id: string | null; stock_artifact_id: string; state: JobState; tender: Tender | null; error: ErrorInfo | null;
  stock: {filename: string; sha256: string; row_count: number} | null;
  analysis: {status: AnalysisStatus; coverage_reason: string | null; benefit_clamped: boolean; model_error: ErrorInfo | null;
    model: string | null; positions: Position[]; economics: Economics} | null}
