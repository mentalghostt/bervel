"""Read-only bounded previews of verified originals, independent of extracted datasets."""
from zlib import error as ZlibError
from pathlib import PurePath
from bervel.core.errors import CoreError
from bervel.core.csv_memory import decode_text, read_csv


def preview(content: bytes, filename: str, media_type: str, *, cursor=0, limit=100) -> dict:
    result={'kind':'unsupported','media_type':media_type,'columns':[],'rows':[],
        'total_rows':0,'next_cursor':None,'text':'','notice':'Для этого формата нет встроенного просмотра. Можно скачать оригинал.'}
    def binary(kind, mime):
        return {**result,'kind':kind,'media_type':mime,'notice':''}
    if not content:
        return {**result,'notice':'В файле нет содержимого для просмотра.'}
    if content.startswith(b'%PDF-'): return binary('pdf','application/pdf')
    signatures=((b'\x89PNG\r\n\x1a\n','image/png'),(b'\xff\xd8\xff','image/jpeg'),
        (b'GIF87a','image/gif'),(b'GIF89a','image/gif'),(b'BM','image/bmp'))
    for prefix,mime in signatures:
        if content.startswith(prefix): return binary('image',mime)
    if content.startswith(b'RIFF') and content[8:12]==b'WEBP': return binary('image','image/webp')
    suffix=PurePath(filename.casefold()).suffix
    if suffix=='.pdf' or media_type=='application/pdf' or media_type.startswith('image/'):
        return {**result,'notice':'Содержимое не соответствует поддерживаемому изображению/PDF. Скачайте оригинал.'}
    try:
        if suffix in {'.docx','.xlsx'}:
            from bervel.core.infrastructure.office_reader import office_text
            text=office_text(content, filename)
            if not text.strip(): return {**result,'notice':'В документе нет доступного текста.'}
            return {**result,'kind':'text','media_type':'text/plain','text':text,
                'notice':'Текстовое представление документа; исходное оформление доступно в оригинале.'}
        if suffix=='.csv' or media_type in {'text/csv','application/csv'}:
            header,data,encoding,truncated=read_csv(content)
            notices=['Показаны только первые 10000 строк. Полные данные доступны в оригинале.'] if truncated else []
            width=max([len(header),*(len(row) for row in data)])
            columns=[value or f'Колонка {i+1}' for i,value in enumerate(header)]
            columns += [f'Колонка {i+1}' for i in range(len(columns),width)]
            if any(len(row)!=len(header) for row in data): notices.append('В строках разное число полей; пустые ячейки дополнены для просмотра.')
            if encoding!='UTF-8': notices.append('Кодировка: '+encoding+'.')
            rows=[row+['']*(width-len(row)) for row in data[cursor:cursor+limit]]
            return {**result,'kind':'table','media_type':'text/csv','columns':columns,'rows':rows,
                'total_rows':len(data),'next_cursor':cursor+limit if cursor+limit<len(data) else None,'notice':' '.join(notices)}
        text,encoding=decode_text(content)
        if not text.strip(): return {**result,'notice':'В файле нет доступного содержимого.'}
        if any(ord(char)<32 and char not in '\r\n\t' for char in text): return result
        if len(text)>200000: notice='Первые 200000 символов. Полный текст доступен в оригинале.'
        else: notice='Текстовое представление; HTML и команды не выполняются.'
        return {**result,'kind':'text','media_type':'text/plain','text':text[:200000],'notice':notice}
    except (UnicodeError,ValueError,OSError,CoreError,ZlibError):
        return {**result,'notice':'Не удалось прочитать содержимое: повреждённый или неподдерживаемый формат. Скачайте оригинал.'}
