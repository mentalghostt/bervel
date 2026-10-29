"""OpenAI embedding HTTP adapter. Credentials and upstream bodies never escape."""
from collections import deque
import ipaddress
import json
import math
from threading import Lock
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from bervel.core.errors import CoreError
from bervel.core.chunking import BATCH_ITEMS,BATCH_TOKENS

MODEL = 'text-embedding-3-small'
DIMENSIONS = 1536


class OpenAIEmbeddings:
    max_tokens=8192
    def __init__(self, key: str, *, base_url: str = 'https://api.openai.com/v1',
                 dimensions: int = DIMENSIONS, min_interval: float = 5, token_budget: int = 32000):
        url = urlsplit(base_url)
        loopback = url.hostname == 'localhost'
        try: loopback = loopback or ipaddress.ip_address(url.hostname or '').is_loopback
        except ValueError: pass
        if (url.scheme != 'https' and not (url.scheme == 'http' and loopback)) or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Embedding API requires HTTPS without URL credentials')
        if type(token_budget)!=int or not BATCH_TOKENS<=token_budget<=32000:
            raise ValueError('Embedding token budget must be between 8000 and 32000')
        self.key,self.base_url,self.dimensions,self.min_interval = key,base_url.rstrip('/'),dimensions,min_interval
        self.space=MODEL+':'+str(dimensions)
        self.token_budget=self.budget_cap=token_budget
        self.encoder=None
        self.lock,self.last,self.window = Lock(),None,deque()

    def count_tokens(self,value:str)->int:
        if self.encoder is None:
            import tiktoken
            self.encoder=tiktoken.get_encoding('cl100k_base')
        return len(self.encoder.encode(value,disallowed_special=()))

    def embed(self, texts: list[str], *, purpose: str = 'document') -> list[list[float]]:
        if not self.key:
            raise CoreError('EMBEDDING_UNCONFIGURED', 'Не настроен BERVEL_EMBEDDING_API_KEY. Оригинал сохранён; индексация ожидает подключения OpenAI API.')
        if not texts or len(texts)>BATCH_ITEMS or any(not isinstance(t,str) or not t.strip() or len(t.encode())>6000 for t in texts):
            raise CoreError('KNOWLEDGE_CONTEXT_LIMIT', 'Некорректная или слишком большая порция эмбеддингов.')
        size = sum(self.count_tokens(t) for t in texts)
        if size > BATCH_TOKENS:
            raise CoreError('KNOWLEDGE_CONTEXT_LIMIT', 'Порция эмбеддингов превышает 8000 токенов.')
        with self.lock:
            while True:
                now=monotonic()
                while self.window and now-self.window[0][0]>=60: self.window.popleft()
                delay=max(0,self.min_interval-(now-self.last)) if self.last is not None else 0
                shortage=sum(n for _,n in self.window)+size-self.token_budget
                if shortage>0:
                    for stamp,used in self.window:
                        shortage-=used
                        if shortage<=0:
                            delay=max(delay,60-(now-stamp)); break
                if delay<=0: break
                sleep(delay)
            self.last=monotonic(); self.window.append((self.last,size))
            request=Request(self.base_url+'/embeddings',data=json.dumps({'model':MODEL,'input':texts,
                'dimensions':self.dimensions,'encoding_format':'float'},ensure_ascii=False).encode(),
                headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'},method='POST')
            try:
                with urlopen(request,timeout=45) as response:
                    raw=response.read(5*1024*1024+1)
                    limit=response.headers.get('x-ratelimit-limit-tokens')
                    if limit and limit.isdigit(): self.token_budget=min(self.budget_cap,max(8000,int(limit)*4//5))
                if len(raw)>5*1024*1024: raise ValueError()
                data=json.loads(raw)
                if data.get('model')!=MODEL or len(data['data'])!=len(texts): raise ValueError()
                vectors={}
                for item in data['data']:
                    index,vector=item['index'],item['embedding']
                    if type(index)!=int or not 0<=index<len(texts) or index in vectors or not isinstance(vector,list) or len(vector)!=self.dimensions:
                        raise ValueError()
                    if any(type(n) not in (int,float) or not math.isfinite(n) for n in vector): raise ValueError()
                    norm=sum(n*n for n in vector)
                    if not math.isfinite(norm) or norm<=0: raise ValueError()
                    vectors[index]=[float(n) for n in vector]
                return [vectors[i] for i in range(len(texts))]
            except HTTPError as error:
                try:
                    payload=json.loads(error.read(8192))
                    problem=payload.get('error',{}) if isinstance(payload,dict) else {}
                except (ValueError,OSError): problem={}
                finally: error.close()
                region=isinstance(problem,dict) and problem.get('code')=='unsupported_country_region_territory'
                code='EMBEDDING_REGION_UNAVAILABLE' if error.code==403 and region else 'EMBEDDING_RATE_LIMIT' if error.code==429 else 'EMBEDDING_AUTH_ERROR' if error.code in (401,403) else 'EMBEDDING_UNAVAILABLE'
                message={'EMBEDDING_REGION_UNAVAILABLE':'OpenAI недоступен из текущей сети: регион не поддерживается. Готовые фрагменты сохранены; автоматического повтора нет.',
                         'EMBEDDING_RATE_LIMIT':'OpenAI ограничил частоту или квоту эмбеддингов. Индексация остановлена; автоматического повтора нет.',
                         'EMBEDDING_AUTH_ERROR':'OpenAI отклонил доступ к эмбеддингам. Проверьте API-ключ и разрешения.'}.get(code,
                         'OpenAI Embeddings недоступен. Готовые порции сохранены; автоматического повтора нет.')
                raise CoreError(code,message) from None
            except (URLError,TimeoutError,OSError):
                raise CoreError('EMBEDDING_UNAVAILABLE','Не удалось завершить запрос OpenAI Embeddings. Готовые порции сохранены; автоматического повтора нет.') from None
            except (ValueError,TypeError,KeyError,OverflowError):
                raise CoreError('EMBEDDING_INVALID_RESPONSE','OpenAI вернул непроверяемые векторы. Порция не опубликована.') from None
