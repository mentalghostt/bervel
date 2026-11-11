import {readSaveCommand} from './companySettingsApi'
import type {SaveCommand} from './companySettingsApi'

export const companyRecoveryKey = 'bervel.company-settings.pending.v1'

function transaction<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return new Promise((resolve,reject) => {
    const opening = indexedDB.open('bervel.company-settings',1)
    opening.onupgradeneeded = () => opening.result.createObjectStore('commands')
    opening.onerror = () => reject(opening.error)
    opening.onsuccess = () => {
      const database = opening.result
      const tx = database.transaction('commands',mode)
      const request = action(tx.objectStore('commands'))
      let result: T
      request.onsuccess = () => {result = request.result}
      tx.oncomplete = () => {database.close();resolve(result)}
      tx.onabort = tx.onerror = () => {database.close();reject(tx.error)}
    }
  })
}

export async function rememberCompanySave(command: SaveCommand): Promise<void> {
  await transaction('readwrite',store => store.put(command,command.command_key))
  sessionStorage.setItem(companyRecoveryKey,JSON.stringify({storage:'indexeddb',command_key:command.command_key}))
}

function clearReference(key: string): void {
  const raw = sessionStorage.getItem(companyRecoveryKey)
  if (raw) {
    const marker = JSON.parse(raw)
    if (marker.command_key === key) sessionStorage.removeItem(companyRecoveryKey)
  }
}

export async function restoreCompanySave(key: string): Promise<SaveCommand | null> {
  const value: unknown = await transaction('readonly',store => store.get(key))
  if (typeof value === 'object' && value !== null && 'state' in value && value.state === 'saved') {
    clearReference(key)
    return null
  }
  const command = readSaveCommand(value)
  if (command.command_key !== key) throw new Error('Не совпадает сохранённая команда настроек.')
  return command
}

export async function settleCompanySave(command: SaveCommand, accepted: boolean): Promise<void> {
  // Duplicated tabs inherit the marker. Keep a small receipt after success so
  // their references can resolve without retaining another full profile.
  if (accepted) await transaction('readwrite',store => store.put({state:'saved'},command.command_key))
  // Rejected/unknown drafts remain recoverable by another tab with that marker.
  clearReference(command.command_key)
}
