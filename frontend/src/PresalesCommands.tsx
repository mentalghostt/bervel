import { useEffect, useRef, useState } from 'react'
import { executePresalesCommand, preparePresalesCommand, presalesPendingKey, readPendingPresalesCommand } from './presalesApi'
import type { PresalesCommand } from './presalesApi'

export function usePresalesCommands(onComplete: (command: PresalesCommand, result: unknown) => void) {
  const [pending, setPending] = useState<PresalesCommand | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const callback = useRef(onComplete)
  useEffect(() => { callback.current = onComplete }, [onComplete])
  useEffect(() => {
    function restore() {
      try { const command = readPendingPresalesCommand(); if (command) setPending(command) }
      catch (reason) { setError(reason instanceof Error ? reason.message : 'Не удалось восстановить команду.') }
    }
    restore()
    const listener = (event: StorageEvent) => { if (event.key === presalesPendingKey) restore() }
    window.addEventListener('storage', listener)
    return () => window.removeEventListener('storage', listener)
  }, [])
  async function execute(command: PresalesCommand) {
    if (inFlight.current) return
    inFlight.current = true; setBusy(true); setError(''); setPending(command)
    try { const result = await executePresalesCommand(command); setPending(null); callback.current(command, result) }
    catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Не удалось выполнить команду.')
      try { setPending(readPendingPresalesCommand()) } catch { /* Preserve the recoverable draft and original storage error. */ }
    } finally { inFlight.current = false; setBusy(false) }
  }
  async function run(path: string, body: Record<string, unknown>, method: 'POST' | 'PUT' = 'POST') {
    if (inFlight.current || pending) return
    try { await execute(preparePresalesCommand(path, body, method)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Не удалось сохранить команду перед отправкой.') }
  }
  function syncPending() {
    try { setPending(readPendingPresalesCommand()) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Не удалось восстановить команду.') }
  }
  return { pending, error, busy, run, syncPending, retry: () => { if (pending) void execute(pending) } }
}
