from bervel.core.infrastructure.document_reader import DocumentReader


def pdf_bytes(message='DOCUMENT_PDF_7621 price 318'):
    stream = f'BT /F1 22 Tf 40 160 Td ({message}) Tj ET'.encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 220] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    return pack_pdf(objects)


def pack_pdf(objects):
    result, positions = b'%PDF-1.4\n', []
    for number, obj in enumerate(objects, 1):
        positions.append(len(result)); result += f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n'
    offset = len(result)
    result += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    result += b''.join(f'{position:010d} 00000 n \n'.encode() for position in positions)
    return result + f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{offset}\n%%EOF\n'.encode()


def test_image_only_pdf_is_actually_ocr_read_with_page_locator(tmp_path):
    import subprocess
    import zlib
    path = tmp_path / 'text.pdf'; path.write_bytes(pdf_bytes('SCANNED_PDF_5342 quantity 91'))
    prefix = tmp_path / 'raster'
    subprocess.run(['pdftoppm', '-scale-to', '1800', '-singlefile', str(path), str(prefix)], check=True, capture_output=True)
    magic, dimensions, maximum, pixels = prefix.with_suffix('.ppm').read_bytes().split(b'\n', 3)
    assert magic == b'P6' and maximum == b'255'
    width, height = dimensions.split()
    image = zlib.compress(pixels)
    stream = b'q 600 0 0 220 0 0 cm /Scan Do Q'
    scanned = pack_pdf([b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 220] /Resources << /XObject << /Scan 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /XObject /Subtype /Image /Width ' + width + b' /Height ' + height + b' /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length ' + str(len(image)).encode() + b' >>\nstream\n' + image + b'\nendstream',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream'])
    assert b'SCANNED_PDF' not in scanned  # PDF contains only a raster, no hidden text layer.
    result = DocumentReader().extract(scanned, 'scanned.pdf', 'application/pdf')
    assert result['status'] == 'ready'
    assert result['chunks'][0]['unit'] == 'page' and result['chunks'][0]['row_start'] == 1
    excerpt = result['chunks'][0]['excerpt']
    assert '91' in excerpt and 'SCANNED' in excerpt and '5342' in excerpt


def test_pdf_text_has_page_locator_and_preserves_content():
    result = DocumentReader().extract(pdf_bytes(), 'contract.pdf', 'application/pdf')
    assert result['status'] == 'ready'
    assert result['chunks'][0]['unit'] == 'page' and result['chunks'][0]['row_start'] == 1
    assert 'DOCUMENT_PDF_7621' in result['chunks'][0]['excerpt'] and '318' in result['chunks'][0]['excerpt']


def test_real_image_ocr_and_unsupported_content_are_explicit(tmp_path):
    import subprocess
    original = tmp_path / 'original.pdf'; original.write_bytes(pdf_bytes('IMAGE_TOKEN_9374 quantity 76'))
    prefix = tmp_path / 'picture'
    subprocess.run(['pdftoppm', '-scale-to', '1800', '-singlefile', '-png', str(original), str(prefix)], check=True, capture_output=True)
    result = DocumentReader().extract(prefix.with_suffix('.png').read_bytes(), 'scan.png', 'image/png')
    assert result['status'] == 'ready' and result['chunks'][0]['unit'] == 'image'
    assert '76' in result['chunks'][0]['excerpt']
    assert 'IMAGE_TOKEN' in result['chunks'][0]['excerpt']
    assert DocumentReader().extract(b'%PDF-broken', 'bad.pdf', 'application/pdf')['status'] == 'metadata_only'
    assert DocumentReader().extract(b'\x00\xffopaque', 'model.custom', 'application/octet-stream')['status'] == 'metadata_only'


def test_text_line_locators_and_limits_are_honest():
    result = DocumentReader().extract(b'FIRST_891\nSECOND_673\n', 'data.txt', 'text/plain')
    assert result['status'] == 'ready' and result['chunks'][0]['row_end'] == 2
    assert result['chunks'][0]['unit'] == 'line'
    result = DocumentReader().extract(b'x' * 5000001, 'huge.txt', 'text/plain')
    assert result['status'] == 'partial'
