import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

export default function WorkspaceDialog({ title, onClose, children, wide = false, className = '' }: {
  title: string; onClose: () => void; children: ReactNode; wide?: boolean; className?: string
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current!
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialog.showModal()
    return () => { dialog.close(); opener?.focus() }
  }, [])
  return <dialog ref={ref} className={`workspace-dialog${wide ? ' wide' : ''}${className ? ' ' + className : ''}`} aria-labelledby="workspace-dialog-title"
    onKeyDown={event => {
      if (event.key !== 'Tab') return
      const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, summary, [tabindex]')].filter(element => element.tabIndex >= 0 && !element.matches(':disabled') && element.getClientRects().length > 0)
      const first = controls[0], last = controls.at(-1)
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }}
    onCancel={event => { event.preventDefault(); onClose() }}>
    <header className="dialog-heading"><h2 id="workspace-dialog-title">{title}</h2>
      <button autoFocus type="button" className="circle-button" aria-label="Закрыть диалог" onClick={onClose}>
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          <path d="m4 4 8 8m0-8-8 8" />
        </svg>
      </button>
    </header>
    <div className="dialog-body">{children}</div>
  </dialog>
}
