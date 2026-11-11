"""Fixed safe rendering: model text never becomes executable markup or a template."""
from io import BytesIO
import re
import textwrap
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from bervel.core.errors import CoreError
from bervel.core.infrastructure.pdf_fonts import register_pdf_fonts


def clean(text: str) -> str:
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)


def inline(text: str) -> str:
    # Only markup produced here reaches ReportLab; model HTML is always literal.
    text = escape(text)
    return re.sub(r'\*\*([^*\n]+)\*\*', r'<font name="BervelBold">\1</font>', text)


def pdf_story(content: str, body: ParagraphStyle) -> list:
    story, lines, index, code = [], content.splitlines(), 0, False
    width = A4[0] - 96
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith('```'):
            code = not code
            index += 1
            continue
        if not code and index + 1 < len(lines) and '|' in line:
            cells = lambda value: [part.strip() for part in re.split(r'(?<!\\)\|', value.strip().strip('|'))]
            header, separator = cells(line), cells(lines[index + 1])
            if len(header) == len(separator) and all(re.fullmatch(r':?-{3,}:?', cell) for cell in separator):
                rows, index = [header], index + 2
                while index < len(lines) and '|' in lines[index] and lines[index].strip():
                    row = cells(lines[index])
                    if len(row) != len(header):
                        break
                    rows.append(row)
                    index += 1
                cell_style = ParagraphStyle('cell', parent=body, fontSize=9, leading=13, spaceAfter=0)
                table = Table([[Paragraph(inline(cell), cell_style) for cell in row] for row in rows],
                    colWidths=[width / len(header)] * len(header), repeatRows=1, hAlign='LEFT')
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e5eee9')),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bbc9c1')),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
                    ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ]))
                story.extend([table, Spacer(1, 12)])
                continue
        heading = re.match(r'^(#{1,6})\s+(.+)$', line) if not code else None
        bullet = re.match(r'^\s*([-*]|\d+[.)])\s+(.+)$', line) if not code else None
        if heading:
            level, title = len(heading[1]), heading[2]
            font_size = 17 if level == 1 else 14 if level == 2 else 11
            style = ParagraphStyle('section', parent=body, fontName='BervelBold', fontSize=font_size,
                leading=font_size + 6, spaceBefore=10, spaceAfter=8, keepWithNext=True)
            story.append(Paragraph(inline(title), style))
        elif bullet:
            style = ParagraphStyle('item', parent=body, leftIndent=14, firstLineIndent=0)
            story.append(Paragraph(inline(bullet[2]), style,
                bulletText='•' if bullet[1] in {'-', '*'} else bullet[1]))
        elif line.strip():
            story.append(Paragraph(escape(line) if code else inline(line), body))
        else:
            story.append(Spacer(1, 8))
        index += 1
    return story


class SafeDocumentRenderer:
    def render(self, format: str, title: str, content: str) -> tuple[bytes, str]:
        title, content = clean(title), clean(content)
        if format == 'pdf':
            register_pdf_fonts()
            output = BytesIO()
            document = SimpleDocTemplate(output, pagesize=A4, title=title, author='BERVEL',
                invariant=1, leftMargin=48, rightMargin=48, topMargin=45, bottomMargin=45)
            heading = ParagraphStyle('heading', fontName='BervelBold', fontSize=19, leading=25, spaceAfter=18)
            body = ParagraphStyle('body', fontName='Bervel', fontSize=10, leading=15, spaceAfter=6, splitLongWords=True)
            story = [Paragraph(escape(title), heading)]
            story.extend(pdf_story(content, body))
            document.build(story)
            return output.getvalue(), 'application/pdf'
        if format == 'docx':
            output = BytesIO()
            namespace = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
            root = Element('w:document', {'xmlns:w': namespace})
            body = SubElement(root, 'w:body')
            for line in [title, *content.splitlines()]:
                paragraph = SubElement(body, 'w:p')
                run = SubElement(paragraph, 'w:r')
                SubElement(run, 'w:t', {'xml:space': 'preserve'}).text = line
            SubElement(body, 'w:sectPr')
            with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
                archive.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
                archive.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
                archive.writestr('word/document.xml', tostring(root, encoding='utf-8', xml_declaration=True))
            return output.getvalue(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        if format == 'svg':
            nodes = [textwrap.wrap(line, 82, replace_whitespace=False) or [''] for line in content.splitlines() if line.strip()]
            height = 95 + sum(max(64, len(lines)*22+28)+34 for lines in nodes)
            root = Element('svg', {'xmlns': 'http://www.w3.org/2000/svg', 'width': '960', 'height': str(height),
                'viewBox': f'0 0 960 {height}', 'role': 'img', 'aria-label': title})
            SubElement(root, 'title').text = title
            SubElement(root, 'rect', {'width': '960', 'height': str(height), 'fill': '#12201c'})
            SubElement(root, 'text', {'x': '48', 'y': '48', 'fill': '#b5efd9', 'font-family': 'sans-serif', 'font-size': '24'}).text = title
            y = 82
            for number, lines in enumerate(nodes):
                box = max(64, len(lines)*22+28)
                if number:
                    SubElement(root, 'path', {'d': f'M480 {y-34} V{y-8} M474 {y-15} L480 {y-8} L486 {y-15}',
                        'fill': 'none', 'stroke': '#b5efd9', 'stroke-width': '2'})
                SubElement(root, 'rect', {'x': '48', 'y': str(y), 'width': '864', 'height': str(box), 'rx': '12',
                    'fill': '#20382e', 'stroke': '#87c6ae'})
                for offset, line in enumerate(lines):
                    SubElement(root, 'text', {'x': '72', 'y': str(y+28+offset*22), 'fill': '#f2f8f5',
                        'font-family': 'sans-serif', 'font-size': '17'}).text = line
                y += box+34
            return tostring(root, encoding='utf-8', xml_declaration=True), 'image/svg+xml'
        if format in {'txt', 'csv'}:
            return content.encode('utf-8'), 'text/plain' if format == 'txt' else 'text/csv'
        raise CoreError('VALIDATION_ERROR', 'Поддерживаются PDF, DOCX, SVG, TXT и CSV.')
