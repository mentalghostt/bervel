import { lazy, Suspense, useEffect, useState } from 'react'
import FilesPage from './FilesPage'
import TestChat from './TestChat'
import ProviderSettings from './ProviderSettings'
import TicketsPage from './TicketsPage'

const AiTenderRadarPage = lazy(() => import('./AiTenderRadarPage'))

export default function App() {
  const [view, setView] = useState(window.location.hash)
  const [chatOpened, setChatOpened] = useState(window.location.hash === '#/chat')
  const [inspectionOpened, setInspectionOpened] = useState(window.location.hash === '#/inspection')
  const chatView = view === '#/chat'
  const inspectionView = view === '#/inspection'
  const settingsView = view === '#/settings'
  const aiRadarView = view.split('?')[0] === '#/ai-tender-radar'
  const ticketsView = view.split('?')[0] === '#/tickets'
  const filesView = !chatView && !inspectionView && !settingsView && !aiRadarView && !ticketsView

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
          {[{ href: '#/ai-tender-radar', label: 'Радар ИИ', active: aiRadarView }, { href: '#/files', label: 'Файлы', active: filesView }, { href: '#/chat', label: 'Ассистент', active: chatView }, { href: '#/inspection', label: 'Проверка', active: inspectionView }, { href: '#/tickets', label: 'Обращения', active: ticketsView }, { href: '#/settings', label: 'Настройки', active: settingsView }].map(({ href, label, active }) => (
            <a key={href} href={href} aria-current={active ? 'page' : undefined}><span>{label}</span></a>
          ))}
        </nav>
      </header>
      <div className="app-content">
      {chatOpened && <TestChat active={chatView} />}
      {inspectionOpened && <TestChat active={inspectionView} mode="inspection" />}
      {settingsView && <ProviderSettings />}
      {aiRadarView && <Suspense fallback={<main><p role="status">Открываем Тендерный радар ИИ…</p></main>}><AiTenderRadarPage /></Suspense>}
      {ticketsView && <TicketsPage key={view} />}
      {filesView && <FilesPage key={view.split('?')[1] || ''} />}
      </div>
    </div>
  )
}
