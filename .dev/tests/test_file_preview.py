"""Original preview must not depend on educational CSV extraction or leak another scope."""
from uuid import uuid4
import pytest
from test_api import client,auth,PREFIX


def test_arbitrary_csv_preserves_columns_multiline_values_leading_zeros_and_pagination():
    from bervel.core.infrastructure.file_preview import preview
    content='Артикул;Параметры;Остаток\n0016;"Болт М16; ГОСТ\n7798";9007199254740993.1234\n0020;Шайба;0\n'.encode('utf-8-sig')
    first=preview(content,'stock.csv','text/csv',cursor=0,limit=1)
    assert first['kind']=='table' and first['columns']==['Артикул','Параметры','Остаток']
    assert first['rows']==[['0016','Болт М16; ГОСТ\n7798','9007199254740993.1234']]
    assert first['total_rows']==2 and first['next_cursor']==1
    last=preview(content,'stock.csv','text/csv',cursor=1,limit=1)
    assert last['rows']==[['0020','Шайба','0']] and last['next_cursor'] is None


def test_csv_handles_cp1251_ragged_rows_duplicate_headers_and_markup_as_text():
    from bervel.core.infrastructure.file_preview import preview
    result=preview('Название;Название\n<script>;Болт;ещё\nГайка\n'.encode('cp1251'),'a.csv','text/csv')
    assert result['columns']==['Название','Название','Колонка 3']
    assert result['rows']==[['<script>','Болт','ещё'],['Гайка','','']]
    assert result['notice'] and '1251' in result['notice']


def test_preview_is_bounded_and_does_not_misrepresent_corrupt_csv_or_binary_as_text():
    from bervel.core.infrastructure.file_preview import preview
    wide=preview(('h,'*201+'\nx').encode(),'wide.csv','text/csv')
    assert wide['kind']=='unsupported' and wide['notice']
    malformed=preview(b'a,b\n"unterminated','bad.csv','text/csv')
    assert malformed['kind']=='unsupported'
    binary=preview(b'\x00\xff\x01','data.bin','text/plain')
    assert binary['kind']=='unsupported'
    rows=preview(('id\n'+'1\n'*10001).encode(),'large.csv','text/csv')
    assert rows['total_rows']==10000 and rows['notice']


def test_bom_only_csv_is_reported_without_http_500():
    from bervel.core.infrastructure.file_preview import preview
    assert preview(b'\xef\xbb\xbf','empty.csv','text/csv')['kind']=='unsupported'


def test_corrupt_deflate_office_is_reported_without_http_500():
    import io
    from zipfile import ZipFile,ZIP_DEFLATED
    from bervel.core.infrastructure.file_preview import preview
    stream=io.BytesIO()
    with ZipFile(stream,'w',compression=ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml','<document>broken</document>')
    raw=bytearray(stream.getvalue())
    with ZipFile(io.BytesIO(raw)) as archive: info=archive.getinfo('word/document.xml')
    start=info.header_offset+30+len(info.filename.encode())+len(info.extra)
    raw[start:start+info.compress_size]=b'\xff'*info.compress_size
    assert preview(bytes(raw),'corrupt.docx','application/octet-stream')['kind']=='unsupported'


@pytest.mark.parametrize('content,name,media,kind',[
    (b'%PDF-1.4\n','a.bin','application/octet-stream','pdf'),
    (b'\x89PNG\r\n\x1a\n','a.bin','application/octet-stream','image'),
    (b'<script>alert(1)</script>','a.html','text/html','text'),
    (b'no valid pdf here','a.pdf','application/pdf','unsupported'),
    (b'not an office archive','a.docx','application/octet-stream','unsupported'),
])
def test_preview_identifies_binary_signatures_instead_of_trusting_upload_mime(content,name,media,kind):
    from bervel.core.infrastructure.file_preview import preview
    assert preview(content,name,media)['kind']==kind


def test_preview_reads_verified_original_with_existing_artifact_permissions_and_no_dataset(client):
    http,keys,_=client
    ref=http.post(PREFIX+'/artifacts',headers=auth(keys[0]),data={'command_key':uuid4().hex},
        files={'file':('stock.csv','SKU,Coating,Amount\n0001,zinc,7\n'.encode(),'text/csv')}).json()
    path=PREFIX+'/artifacts/'+ref['artifact_id']+'/preview'
    assert http.get(path).status_code==401
    assert http.get(path,headers=auth(keys[1])).status_code==404
    response=http.get(path,headers=auth(keys[2]))
    assert response.status_code==200 and response.json()['rows']==[['0001','zinc','7']]
    assert http.get(path+'?cursor=-1',headers=auth(keys[0])).status_code==422
    assert http.get(path+'?limit=101',headers=auth(keys[0])).status_code==422
    assert 'dataset' not in http.get(PREFIX+'/artifacts',headers=auth(keys[0])).json()['artifacts'][0]
