import type {Run} from './aiRadarRunsApi'

const format = new Intl.NumberFormat('ru-RU')

export function radarRefreshStatus(run: Pick<Run, 'data'>): string {
  const {data} = run
  if (data.stop_requested) return 'Останавливаем обновление…'
  if (data.state === 'queued') return 'Ожидаю начала обновления'
  if (data.state === 'failed') return data.error?.code === 'USER_STOPPED'
    ? 'Обновление остановлено' : 'Обновление завершилось ошибкой'
  if (data.state === 'complete') return data.completion_summary && data.completion_summary.failed_tenders > 0
    ? `Обновление завершено с замечаниями: ${format.format(data.completion_summary.failed_tenders)} тендеров` : 'База сохранена'
  const checkpoint = data.checkpoint
  const counted = (label: string, completed?: number, total?: number) =>
    completed !== undefined && total !== undefined
      ? `${label}: ${format.format(completed)} из ${format.format(total)}` : label
  switch (data.phase) {
    case 'metrics': return counted('Обновляю показатели', checkpoint?.metrics_completed, checkpoint?.metrics_total)
    case 'search': return 'Поиск тендеров'
    case 'ranking': return counted('Оценка первичной информации', checkpoint?.ranking_completed, checkpoint?.ranking_total)
    case 'details': return 'Подтягиваю детали тендеров'
    case 'analysis': return counted('Анализирую детали тендеров', checkpoint?.analysis_completed, checkpoint?.analysis_total)
    case 'rating': return 'Рассчитываю оценку ИИ'
    default: return 'Подготавливаю обновление'
  }
}
