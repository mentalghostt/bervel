import type {ReviewDecision, RunCard} from './aiRadarRunsApi'

export const resultScore = (card: Pick<RunCard, 'rating'>) => card.rating?.status === 'complete' ? card.rating.score : null

const percent = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2})

export function formatPieceCoverage(value: string) {
  const amount = Number(value)
  if (amount > 0 && amount < 0.005) return '<0,01%'
  if (amount >= 99.995 && amount < 100) return '>99,99%'
  return `${percent.format(amount)}%`
}

const manualChecks = [
  {codes: ['DOCUMENT_REQUIREMENTS_INCOMPLETE', 'DOCUMENT_EXTRACTION_UNKNOWN'],
    reason: 'Из спецификации не удалось подтвердить обязательные условия.',
    task: 'Сверить обязательные технические требования в приложенной спецификации.'},
  {codes: ['DOCUMENTS_NOT_FULLY_READ'],
    reason: 'Часть приложений к закупке недоступна.',
    task: 'Запросить у заказчика приложение, которое не удалось открыть.'},
  {codes: ['DELIVERY_DEADLINE_UNKNOWN', 'DELIVERY_TERMS_UNKNOWN', 'DELIVERY_SCHEDULE_UNKNOWN'],
    reason: 'Без срока поставки нельзя подтвердить исполнение заказа.',
    task: 'Уточнить крайний срок поставки в проекте договора.'},
  {codes: ['LOGISTICS_COST_UNKNOWN'],
    reason: 'Расходы на поставку могут изменить итоговую прибыль.',
    task: 'Уточнить стоимость доставки до места поставки.'},
]

type ReviewSummary = {decision: ReviewDecision; reason: string; paragraphs?: string[]; checks?: string[]; fromModel?: boolean}

export function getReviewSummary(card: RunCard): ReviewSummary | null {
  if (card.state === 'analyzing') return null
  const {assessment, fulfillment, review} = card
  if (card.report_summary && (card.projection?.review_stale || review?.state !== 'complete')
    && !(card.projection?.metrics_review_stale && review?.state === 'complete' && review.decision)) {
    return {...card.report_summary, reason: card.report_summary.paragraphs.join('\n\n')}
  }
  if (review?.state === 'complete' && review.decision) {
    return {decision: review.decision, reason: review.recommendation,
      paragraphs: review.recommendation.split(/\n\s*\n/).filter(Boolean),
      checks: review.decision === 'review' ? review.manual_checks : undefined, fromModel: true}
  }
  const reasons = new Set([
    ...(assessment?.reasons ?? []),
    ...(fulfillment?.reasons ?? []),
    ...(card.completeness?.reasons ?? []),
  ].map(reason => reason.split(':', 1)[0]))
  const profit = assessment?.full_economics?.profit
  if (fulfillment?.status === 'infeasible') {
    const reason = reasons.has('DELIVERY_DEADLINE_PASSED') ? 'Срок поставки уже истёк.'
      : reasons.has('CAPACITY_INSUFFICIENT') ? 'Производственных мощностей не хватит к сроку поставки.'
      : 'Расчёт показывает, что заказ не получится исполнить на заданных условиях.'
    return {decision: 'decline', reason}
  }
  if (reasons.has('PLAN_NOT_PROFITABLE') || (profit != null && Number(profit) <= 0)) {
    return {decision: 'decline', reason: 'Ожидаемая выручка по заказу не покрывает расходы с прибылью.'}
  }
  if (card.metrics?.catalog_match_percent === '0' && card.metrics.catalog_match_incomplete === false
    && card.metrics.catalog_match_unknown === 0 && (card.metrics.catalog_match_total ?? 0) > 0) {
    return {decision: 'decline', reason: 'Среди раскрытых позиций нет продукции, соответствующей ассортименту предприятия.'}
  }
  if (!card.analysis) return null
  if (fulfillment?.status === 'feasible' && (assessment?.score ?? 0) > 0 && Number(profit) > 0
    && card.completeness?.complete !== false) {
    return {decision: 'participate', reason: 'Заказ выполним на заданных условиях и принесёт прибыль.'}
  }
  const check = manualChecks.find(item => item.codes.some(code => reasons.has(code)))
  return check
    ? {decision: 'review', reason: check.reason, checks: [check.task]}
    : {decision: 'review', reason: 'Сохранённых данных пока недостаточно, чтобы подтвердить выгоду и исполнение заказа.'}
}
