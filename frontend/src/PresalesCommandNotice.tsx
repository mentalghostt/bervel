import type { usePresalesCommands } from './PresalesCommands'

export default function PresalesCommandNotice({ command }: { command: ReturnType<typeof usePresalesCommands> }) {
  return <>
    {command.error && <p role="alert" className="presales-alert">{command.error}</p>}
    {command.pending && <div className="presales-recovery" role="status"><p>{command.busy ? 'Ожидаем квитанцию сервера…' : 'Сохранена незавершённая команда. Проверьте её исход перед следующими изменениями.'}</p><button type="button" className="secondary" disabled={command.busy} onClick={command.retry}>Повторить сохранённую команду</button></div>}
  </>
}
