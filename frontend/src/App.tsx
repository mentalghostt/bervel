import { useEffect, useState } from 'react'
import FilesPage from './FilesPage'
import TestChat from './TestChat'
import ProviderSettings from './ProviderSettings'
import RadarPage from './RadarPage'
import TicketsPage from './TicketsPage'

export default function App() {
  const [view, setView] = useState(window.location.hash)
  const [chatOpened, setChatOpened] = useState(window.location.hash === '#/chat')
  const [inspectionOpened, setInspectionOpened] = useState(window.location.hash === '#/inspection')
  const chatView = view === '#/chat'
  const inspectionView = view === '#/inspection'
  const settingsView = view === '#/settings'
  const radarView = view.split('?')[0] === '#/radar'
  const ticketsView = view.split('?')[0] === '#/tickets'
  const filesView = !chatView && !inspectionView && !settingsView && !radarView && !ticketsView

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
          {[{ href: '#/files', label: 'Файлы', active: filesView, icon:'▤' }, { href: '#/radar', label: 'Тендерный радар', active: radarView, icon:'◎' }, { href: '#/chat', label: 'Универсальный ассистент', active: chatView, icon:'✳' }, { href: '#/inspection', label: 'Проверка', active: inspectionView, icon:'⌕' }, { href: '#/tickets', label: 'Обращения', active: ticketsView, icon:'↗' }, { href: '#/settings', label: 'Настройки', active: settingsView, icon:'⚙' }].map(({ href, label, active, icon }) => (
            <a key={href} href={href} aria-current={active ? 'page' : undefined}><span aria-hidden="true" className="nav-icon">{icon}</span><span>{label}</span></a>
          ))}
        </nav>
      </header>
      <div className="app-content">
      {chatOpened && <TestChat active={chatView} />}
      {inspectionOpened && <TestChat active={inspectionView} mode="inspection" />}
      {settingsView && <ProviderSettings />}
      {radarView && <RadarPage key={view} />}
      {ticketsView && <TicketsPage key={view} />}
      {filesView && <FilesPage key={view.split('?')[1] || ''} />}
      </div>
    </div>
  )
}
