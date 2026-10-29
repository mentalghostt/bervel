export type TicketFields={title:string;problem:string;expected:string;actual:string;steps:string[];context:string;links:string[]}
export type Ticket=TicketFields&{ticket_id:string;status:'created'|'sent';revision:number;created_at:string;updated_at:string}
export type TicketAttempt={command_key:string;expected_revision?:number}&TicketFields
export class TicketRequestError extends Error {
 readonly rejected:boolean
 constructor(message:string,rejected=false){super(message);this.rejected=rejected}
}
async function request(path:string,method='GET',body?:TicketAttempt):Promise<unknown>{
 let response:Response
 try{response=await fetch('/api/v0.1/tickets'+path,{method,headers:{'X-Bervel-Web':'1',...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(30000)})}
 catch{throw Error('Связь с обращениями прервана. Повтор сохранения использует прежнюю команду.')}
 const value=await response.json().catch(()=>null)
 if(!response.ok)throw new TicketRequestError(response.status===409?'Обращение изменилось. Откройте его заново перед правкой.':response.status===404?'Обращение не найдено.':response.status===403?'Недостаточно прав для обращений.':response.status===422?'Проверьте обязательные поля и ссылки.':'Сервис обращений недоступен. Повтор использует прежнюю команду.',[400,401,403,404,409,413,415,422].includes(response.status))
 return value
}
function ticket(value:unknown):Ticket{
 const v=value as Ticket
 if(!v||typeof v.ticket_id!=='string'||typeof v.title!=='string'||!Number.isInteger(v.revision)||!['created','sent'].includes(v.status)||!Array.isArray(v.steps)||!Array.isArray(v.links))throw Error('Неверный ответ сервиса обращений.')
 return v
}
export async function listTickets(cursor=0):Promise<{tickets:Ticket[];next_cursor:number|null}>{
 const value=await request(`?cursor=${cursor}&limit=20`) as {tickets:unknown[];next_cursor:number|null}
 if(!Array.isArray(value?.tickets)||!(value.next_cursor===null||(Number.isInteger(value.next_cursor)&&value.next_cursor>cursor)))throw Error('Не удалось прочитать список обращений.')
 return {tickets:value.tickets.map(ticket),next_cursor:value.next_cursor}
}
export async function readTicket(id:string){return ticket(await request('/'+encodeURIComponent(id)))}
export async function saveTicket(id:string|null,body:TicketAttempt){return ticket(await request(id?'/'+encodeURIComponent(id):'',id?'PUT':'POST',body))}
