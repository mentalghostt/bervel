from io import BytesIO
from zipfile import ZipFile


def test_reads_all_csv_rows_and_xlsx_sheets_after_old_limit(tmp_path):
    from bervel.core.infrastructure.source_sections import iter_source_sections
    from openpyxl import Workbook
    csv = ('name,quantity\n' + ''.join(f'Bolt{i},2\n' for i in range(10005))).encode()
    sections = list(iter_source_sections(csv, 'items.csv', artifact_id='a', workspace=tmp_path))
    assert 'Bolt10004' in sections[-1]['text']
    assert sections[-1]['location']['row'] == 10006
    book = Workbook(); book.active.title = 'First'
    book.active.append(['Name', 'Quantity']); book.active.append(['Bolt', '=1+1'])
    book.create_sheet('Last').append(['Last item', 9])
    content = BytesIO(); book.save(content)
    sections = list(iter_source_sections(content.getvalue(), 'items.xlsx', artifact_id='a', workspace=tmp_path))
    assert sections[-1]['location']['sheet'] == 'Last'
    assert any(not s['complete'] and s['issues'] for s in sections)
    assert all(s['section_id'] and s['artifact_id'] == 'a' for s in sections)


def test_docx_preserves_table_row_coordinates_and_escapes(tmp_path):
    from bervel.core.infrastructure.source_sections import iter_source_sections
    content = BytesIO()
    with ZipFile(content, 'w') as z:
        z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Client</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Болт &amp; гайка</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>12</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    sections = list(iter_source_sections(content.getvalue(), 'items.docx', workspace=tmp_path))
    assert sections[1]['location'] == {'table': 1, 'row': 1}
    assert 'Болт & гайка' in sections[1]['text'] and '12' in sections[1]['text']


def test_corrupt_input_is_explicitly_incomplete(tmp_path):
    from bervel.core.infrastructure.source_sections import iter_source_sections
    result = list(iter_source_sections(b'broken', 'bad.pdf', workspace=tmp_path))
    assert result and not result[0]['complete'] and result[0]['issues']


def test_pdf_reads_every_page_after_old_hundred_page_limit(tmp_path):
    from reportlab.pdfgen.canvas import Canvas
    from bervel.core.infrastructure.source_sections import iter_source_sections
    content=BytesIO(); canvas=Canvas(content)
    for i in range(102): canvas.drawString(70,700,f'Item page {i+1}'); canvas.showPage()
    canvas.save()
    sections=list(iter_source_sections(content.getvalue(),'long.pdf',workspace=tmp_path))
    assert len(sections)==102 and all(s['complete'] for s in sections)
    assert sections[-1]['location']['page']==102 and 'Item page 102' in sections[-1]['text']


def test_blank_image_does_not_claim_success(tmp_path):
    from PIL import Image
    from bervel.core.infrastructure.source_sections import iter_source_sections
    content=BytesIO(); Image.new('RGB',(800,400),'white').save(content,format='PNG')
    sections=list(iter_source_sections(content.getvalue(),'blank.png',workspace=tmp_path))
    assert len(sections)==1 and not sections[0]['complete']


def test_docx_table_with_embedded_image_is_incomplete(tmp_path):
    from bervel.core.infrastructure.source_sections import iter_source_sections
    content=BytesIO()
    with ZipFile(content,'w') as z:
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Item</w:t><w:drawing/></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    sections=list(iter_source_sections(content.getvalue(),'mixed.docx',workspace=tmp_path))
    assert sections and not sections[0]['complete']


def test_mixed_pdf_includes_scanned_text_and_does_not_ignore_text_layer(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.utils import ImageReader
    from bervel.core.infrastructure.source_sections import iter_source_sections
    image=Image.new('RGB',(1500,300),'white')
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',44)
    ImageDraw.Draw(image).text((40,50),'SCANNED ITEM BOLT 120 PCS',fill='black',font=font)
    content=BytesIO(); canvas=Canvas(content)
    canvas.drawString(50,760,'Client order with scanned specification')
    canvas.drawImage(ImageReader(image),50,500,width=500,height=100)
    canvas.showPage(); canvas.save()
    sections=list(iter_source_sections(content.getvalue(),'mixed.pdf',workspace=tmp_path))
    assert len(sections)==1 and 'Client order' in sections[0]['text']
    assert 'SCANNED ITEM BOLT 120 PCS' in sections[0]['text']


def test_csv_large_cell_not_cut_by_stdlib_default(tmp_path):
    from bervel.core.infrastructure.source_sections import iter_source_sections
    value='x'*140000
    sections=list(iter_source_sections(('name,qty\n'+value+',1').encode(),'large.csv',workspace=tmp_path))
    assert sections[-1]['complete'] and value in sections[-1]['text']


def test_original_artifact_reader_keeps_file_identity(tmp_path):
    from uuid import UUID
    from bervel.core.models import Artifact, ArtifactRef
    from bervel.modules.presales.infrastructure.source_reader import RequestSourceReader
    id=UUID('00000000-0000-0000-0000-000000000001')
    artifact=Artifact(ArtifactRef(id),{'filename':'items.csv'},b'name,qty\nbolt,2')
    sections=RequestSourceReader(tmp_path).read(artifact)
    assert sections[-1]['artifact_id']==str(id) and sections[-1]['location']['row']==2


def test_utf16_csv_is_decoded_and_malformed_quote_stays_incomplete(tmp_path):
    from bervel.core.infrastructure.source_sections import iter_source_sections
    text='Наименование;Количество\nБолт;12'
    sections=list(iter_source_sections(text.encode('utf-16'),'unicode.csv',workspace=tmp_path))
    assert 'Болт' in sections[-1]['text'] and all(s['complete'] for s in sections)
    broken=list(iter_source_sections(b'name,qty\n"unclosed,12','broken.csv',workspace=tmp_path))
    assert not broken[-1]['complete'] and broken[-1]['issues']
