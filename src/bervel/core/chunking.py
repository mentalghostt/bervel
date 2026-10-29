"""Deterministic evidence boundaries; keep source text and table headers."""
import json
import re

CHUNK_CHARS = 1500
CHUNK_BYTES = 3500
INPUT_BYTES = 6000
CHUNK_VERSION = 1
BATCH_ITEMS=64
BATCH_TOKENS=8000


def split_blocks(blocks: list[dict]) -> list[dict]:
    result = []
    for block in blocks:
        text = block['excerpt']
        prefix=block.get('context_prefix','')
        if prefix and text.startswith(prefix): text=text[len(prefix):]
        if len(prefix.encode())>=CHUNK_BYTES or len(prefix)>=CHUNK_CHARS:
            from bervel.core.errors import CoreError
            raise CoreError('KNOWLEDGE_CONTEXT_LIMIT','Контекст заголовка превышает допустимый размер фрагмента.')
        start = 0
        line=block.get('row_start',0)
        while start < len(text):
            end = min(start + CHUNK_CHARS-len(prefix), len(text))
            while len((prefix+text[start:end]).encode('utf-8')) > CHUNK_BYTES:
                end -= 1
            if end < len(text):
                part = text[start:end]
                # Prefer a complete paragraph; keep delimiters in the evidence.
                boundaries = [m.end() for m in re.finditer(r'\n\s*\n', part)]
                boundaries = [b for b in boundaries if b >= len(part) // 3]
                if not boundaries:
                    boundaries = [m.end() for m in re.finditer(r'\n|(?<=[.!?;])\s+', part)
                                  if m.end() >= len(part) * 2 // 3]
                if boundaries: end = start + boundaries[-1]
            source = {**block, 'excerpt': prefix+text[start:end]}
            if block.get('unit') == 'line':
                source['row_start'] = line
                source['row_end'] = source['row_start'] + text[start:end].rstrip('\n').count('\n')
            result.append(source)
            line+=text[start:end].count('\n')
            start = end
    return result


def table_blocks(header: list[str], rows: list[list[str]]) -> list[dict]:
    prefix = 'Columns: ' + json.dumps(header, ensure_ascii=False) + '\n'
    result = []
    for number, row in enumerate(rows, 1):
        body = f'Row {number}: ' + json.dumps(row, ensure_ascii=False)
        block = dict(excerpt=prefix+body,unit='row',row_start=number,row_end=number,total_rows=len(rows))
        pieces = split_blocks([block])
        for piece in pieces:
            if not piece['excerpt'].startswith(prefix) and len(prefix.encode()) < 1000:
                piece['excerpt'] = prefix + piece['excerpt']
            result.append(piece)
    return result or [dict(excerpt=prefix,unit='row',row_start=0,row_end=0,total_rows=0)]


def embedding_input(filename: str, chunk: dict) -> str:
    # Upload aliases belong to source metadata; identical content shares its vector.
    value = f"{chunk.get('unit','line')} {chunk['row_start']}–{chunk['row_end']}\n" + chunk['excerpt']
    if len(value.encode('utf-8')) > INPUT_BYTES:
        from bervel.core.errors import CoreError
        raise CoreError('KNOWLEDGE_CONTEXT_LIMIT', 'Фрагмент с контекстом превышает предел индексации; исходный файл сохранён.')
    return value


def fit_to_model(filename,blocks,count_tokens,limit):
    """Refine structural chunks for the actual embedding tokenizer, without truncation."""
    result=[]
    def fit(block):
        if count_tokens(embedding_input(filename,block))<=limit:
            result.append(block); return
        value=block['excerpt']
        prefix=block.get('context_prefix','')
        if prefix and value.startswith(prefix): value=value[len(prefix):]
        elif value.startswith('Columns: ') and '\n' in value:
            prefix,value=value.split('\n',1); prefix+='\n'
        if len(value)<2:
            from bervel.core.errors import CoreError
            raise CoreError('KNOWLEDGE_CONTEXT_LIMIT','Заголовок фрагмента превышает контекст embedding-модели.')
        middle=len(value)//2
        boundary=max(value.rfind('\n',0,middle+1),value.rfind('. ',0,middle+1))
        cut=boundary+1 if boundary>middle//2 else middle
        line=block.get('row_start',0)
        for part in (value[:cut],value[cut:]):
            source={**block,'excerpt':prefix+part}
            if block.get('unit')=='line':
                source.update(row_start=line,row_end=line+part.rstrip('\n').count('\n'))
                line+=part.count('\n')
            fit(source)
    for block in blocks: fit(block)
    return result


def text_batches(texts,count_tokens):
    result,current,total=[],[],0
    for value in texts:
        tokens=count_tokens(value)
        if current and (len(current)>=BATCH_ITEMS or total+tokens>BATCH_TOKENS):
            result.append(current);current=[];total=0
        current.append(value);total+=tokens
    if current:result.append(current)
    return result
