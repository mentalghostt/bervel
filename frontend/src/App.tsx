import { lazy, Suspense, useEffect, useState } from 'react'
import FilesPage from './FilesPage'
import TestChat from './TestChat'
import ProviderSettings from './ProviderSettings'
import TicketsPage from './TicketsPage'

const AiTenderRadarPage = lazy(() => import('./AiTenderRadarPage'))
const CompanySettingsPage = lazy(() => import('./CompanySettingsPage'))
const PresalesPage = lazy(() => import('./PresalesPage'))

export default function App() {
  const [view, setView] = useState(window.location.hash)
  const [chatOpened, setChatOpened] = useState(window.location.hash === '#/chat')
  const [inspectionOpened, setInspectionOpened] = useState(window.location.hash === '#/inspection')
  const chatView = view === '#/chat'
  const inspectionView = view === '#/inspection'
  const settingsView = view === '#/settings'
  const companyView = view === '#/company-settings'
  const runsView = view.split('?')[0] === '#/ai-radar-runs'
  const aiRadarView = runsView || view.split('?')[0] === '#/ai-tender-radar'
  const ticketsView = view.split('?')[0] === '#/tickets'
  const presalesView = view.split('?')[0] === '#/presales'
  const presalesSettingsView = view.split('?')[0] === '#/presales-settings'
  const filesView = !chatView && !inspectionView && !settingsView && !aiRadarView && !ticketsView && !companyView && !runsView && !presalesView && !presalesSettingsView

  useEffect(() => {
    function navigate() {
      setView(window.location.hash)
      if (window.location.hash === '#/chat') setChatOpened(true)
      if (window.location.hash === '#/inspection') setInspectionOpened(true)
    }
    window.addEventListener('hashchange', navigate)
    return () => window.removeEventListener('hashchange', navigate)
  }, [])

  return (
    <div className={`app-shell${chatView || inspectionView ? ' chat-active' : ''}`} onDragOver={event => {
      if (event.dataTransfer.types.includes('Files')) {
        event.preventDefault()
        event.dataTransfer.dropEffect = 'none'
      }
    }} onDrop={event => {
      if (event.dataTransfer.types.includes('Files')) event.preventDefault()
    }}>
      <header className="app-topbar">
        <div className="brand-row">
          <div className="flex items-center gap-3">
            <span className="font-display text-xl font-semibold tracking-[0.12em]">BERVEL</span>
          </div>
        </div>
        <nav aria-label="Разделы приложения">
          {[{ href: '#/presales', label: 'Пресейл-конвейер', active: presalesView || presalesSettingsView }, { href: '#/ai-tender-radar', label: 'Радар ИИ', active: runsView || aiRadarView }, { href: '#/files', label: 'Файлы', active: filesView }, { href: '#/chat', label: 'Ассистент', active: chatView }, { href: '#/inspection', label: 'Проверка', active: inspectionView }, { href: '#/tickets', label: 'Обращения', active: ticketsView }, { href: '#/company-settings', label: 'Настройки предприятия', active: companyView }, { href: '#/settings', label: 'Настройки', active: settingsView }].map(({ href, label, active }) => (
            <a key={href} href={href} aria-current={active ? 'page' : undefined}><span>{label}</span></a>
          ))}
        </nav>
      </header>
      <div className="app-content">
      {chatOpened && <TestChat active={chatView} />}
      {inspectionOpened && <TestChat active={inspectionView} mode="inspection" />}
      {settingsView && <ProviderSettings />}
      {(presalesView || presalesSettingsView) && <Suspense fallback={<main aria-label="Пресейл-конвейер"><p role="status">Открываем пресейл…</p></main>}><PresalesPage key={view} initialSettingsOpen={presalesSettingsView} /></Suspense>}
      {companyView && <Suspense fallback={<main aria-label="Настройки предприятия"><p role="status">Открываем настройки предприятия…</p></main>}><CompanySettingsPage /></Suspense>}
      {aiRadarView && <Suspense fallback={<main aria-label="Радар ИИ"><p role="status">Открываем Тендерный радар ИИ…</p></main>}><AiTenderRadarPage /></Suspense>}
      {ticketsView && <TicketsPage key={view} />}
      {filesView && <FilesPage key={view.split('?')[1] || ''} />}
      </div>
    </div>
  )
}
