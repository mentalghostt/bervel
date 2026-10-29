"""Controlled originals for actual model acceptance; no credentials required."""
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_document_reader import pdf_bytes

folder = Path(__file__).resolve().parents[1] / 'local/file-memory/live'
folder.mkdir(parents=True, exist_ok=True)
(folder / 'memory-note-4812.txt').write_text('NOTE_TOKEN_4812\nwarehouse=KAZAN\nreserve=246\n', encoding='utf-8')
(folder / 'memory-contract-7621.pdf').write_bytes(pdf_bytes('DOCUMENT_PDF_7621 price 318'))
picture = pdf_bytes('IMAGE_TOKEN_9374 quantity 76')
# Render a blue rectangle underneath the printed marker. The model must see it.
# Rebuild offsets rather than altering a generated PDF in place.
stream = b'0 0 1 rg 40 25 100 60 re f 0 0 0 rg BT /F1 22 Tf 40 160 Td (IMAGE_TOKEN_9374 quantity 76) Tj ET'
objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 220] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
result, positions = b'%PDF-1.4\n', []
for number, obj in enumerate(objects, 1):
    positions.append(len(result)); result += f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n'
offset = len(result)
result += b'xref\n0 6\n0000000000 65535 f \n' + b''.join(f'{pos:010d} 00000 n \n'.encode() for pos in positions)
result += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{offset}\n%%EOF\n'.encode()
render = folder / 'image-source.pdf'; render.write_bytes(result)
subprocess.run(['pdftoppm', '-scale-to', '1800', '-singlefile', '-png', str(render), str(folder / 'memory-picture-9374')], check=True)
(folder / 'memory-drawing-4826.custom').write_bytes(b'\x00\xff\x01OPAQUE_BINARY_4826')
(folder / 'memory-stock-6318.csv').write_text('name,quantity,unit\nNEW_CSV_TOKEN_6318,1374,pcs\n', encoding='utf-8')
print('Created controlled TXT, PDF, PNG, CSV and unknown-format originals')
