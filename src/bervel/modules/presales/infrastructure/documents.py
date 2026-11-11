"""Fixed BERVEL quotation layout. Customer fields are escaped, never executable markup."""
from io import BytesIO
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
import re
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle

from bervel.core.infrastructure.pdf_fonts import register_pdf_fonts
from ..pricing import line_amount


def _clean(value) -> str:
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value if value is not None else ''))


def _description(line):
    return '\n'.join([_clean(line['name'])]+[f"{_clean(r['name'])}: {_clean(r['value'])}" for r in line.get('requirements',[])])


def _ready_text(value) -> str:
    """Format stored dates for the client without assuming an absent timezone."""
    if not isinstance(value,str) or not value: return 'Уточняется'
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
            return date.fromisoformat(value).strftime('%d.%m.%Y')
        parsed=datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None: return 'Уточняется'
        return parsed.astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M МСК')
    except ValueError:
        return 'Уточняется'


def _rows(offer):
    yield ['№','Наименование и характеристики','Кол-во','Ед.','Цена','Сумма','Срок']
    for i,line in enumerate(offer['lines'],1):
        yield [str(i),_description(line),line['quantity'],line.get('unit') or 'Уточн.',
            f"{line['unit_price']} {line['currency']}",f"{line['line_total']} {line['currency']}",_ready_text(line.get('ready_at'))]


def _validate_totals(offer):
    totals={}
    try:
        for line in offer['lines']:
            quantity=Decimal(line['quantity']); price=Decimal(line['unit_price']); total=Decimal(line['line_total'])
            if not all(value.is_finite() and value>=0 for value in (quantity,price,total)) or quantity<=0:
                raise ValueError('Invalid quotation total values')
            currency=line['currency']
            expected=line_amount(unit_price=price,quantity=quantity,currency=currency)
            if total!=expected: raise ValueError('Inconsistent quotation line total')
            with localcontext() as context:
                context.prec=100
                totals[currency]=totals.get(currency,Decimal(0))+total
        if {key:Decimal(value) for key,value in offer['totals'].items()}!=totals:
            raise ValueError('Inconsistent quotation totals')
    except (InvalidOperation,KeyError,TypeError) as error:
        raise ValueError('Invalid quotation totals') from error


def _notes(offer):
    for currency,total in sorted(offer['totals'].items()): yield f'Итого: {total} {currency}'
    if offer.get('offer_terms'): yield 'Условия: '+_clean(offer['offer_terms'])
    for label,key in [('Уточнения','clarifications'),('Не включено в стоимость','unsupported')]:
        if offer.get(key):
            yield label
            for item in offer[key]:
                quantity=(' — '+_clean(item['quantity'])+' '+_clean(item.get('unit'))) if item.get('quantity') is not None else ''
                yield _clean(item.get('name',''))+quantity+': '+_clean(item.get('message','Требует согласования.'))


class OfferRenderer:
    def __init__(self, workspace: Path | None = None):
        self.workspace=workspace

    def render_pdf(self, offer: dict) -> bytes:
        _validate_totals(offer)
        register_pdf_fonts(); output=BytesIO()
        body=ParagraphStyle('OfferBody',fontName='Bervel',fontSize=9,leading=13,spaceAfter=8)
        heading=ParagraphStyle('OfferHeading',parent=body,fontName='BervelBold',fontSize=20,leading=25,spaceAfter=14)
        cell=ParagraphStyle('OfferCell',parent=body,fontSize=8,leading=11,spaceAfter=0,splitLongWords=True)
        p=lambda text,style=body:Paragraph(escape(_clean(text)).replace('\n','<br/>'),style)
        story=[p('BERVEL',heading),p('Коммерческое предложение',heading),
            p(f"№ {offer['number']} от {_ready_text(offer.get('date'))}"),p('Клиент: '+(offer.get('client') or 'Уточняется'))]
        if offer.get('seller_contacts'): story.append(p('Контакты: '+offer['seller_contacts']))
        story.append(Spacer(1,10))
        rows=[[p(value,cell) for value in row] for row in _rows(offer)]
        table=LongTable(rows,colWidths=[22,175,43,30,73,76,72],repeatRows=1,hAlign='LEFT',splitInRow=1)
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e7ecef')),
            ('GRID',(0,0),(-1,-1),.3,colors.HexColor('#cbd4da')),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),
            ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        story.extend([table,Spacer(1,16)])
        story.extend(p(note) for note in _notes(offer))
        def page(canvas,doc):
            canvas.setFont('Bervel',8); canvas.setFillColor(colors.HexColor('#6a7780'))
            canvas.drawRightString(A4[0]-32,22,f'BERVEL · {doc.page}')
        SimpleDocTemplate(output,pagesize=A4,leftMargin=32,rightMargin=32,topMargin=34,bottomMargin=38,
            title='Коммерческое предложение BERVEL',author='BERVEL',invariant=1).build(story,onFirstPage=page,onLaterPages=page)
        return output.getvalue()

    def render_docx(self, offer: dict) -> bytes:
        _validate_totals(offer)
        ns='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        w=lambda name:'{'+ns+'}'+name
        root=Element(w('document')); body=SubElement(root,w('body'))
        def paragraph(parent,text,style=None):
            p=SubElement(parent,w('p'))
            if style:
                props=SubElement(p,w('pPr')); SubElement(props,w('pStyle'),{w('val'):style})
            for i,part in enumerate(_clean(text).split('\n')):
                run=SubElement(p,w('r'))
                if i: SubElement(run,w('br'))
                SubElement(run,w('t'),{'{http://www.w3.org/XML/1998/namespace}space':'preserve'}).text=part
            return p
        paragraph(body,'BERVEL','Heading1'); paragraph(body,'Коммерческое предложение','Title')
        paragraph(body,f"№ {offer['number']} от {_ready_text(offer.get('date'))}")
        paragraph(body,'Клиент: '+(offer.get('client') or 'Уточняется'))
        if offer.get('seller_contacts'): paragraph(body,'Контакты: '+offer['seller_contacts'])
        table=SubElement(body,w('tbl')); props=SubElement(table,w('tblPr'))
        SubElement(props,w('tblW'),{w('w'):'10560',w('type'):'dxa'}); SubElement(props,w('tblLayout'),{w('type'):'fixed'})
        borders=SubElement(props,w('tblBorders'))
        for edge in ('top','left','bottom','right','insideH','insideV'): SubElement(borders,w(edge),{w('val'):'single',w('sz'):'4',w('color'):'CBD4DA'})
        margins=SubElement(props,w('tblCellMar'))
        for edge in ('top','left','bottom','right'): SubElement(margins,w(edge),{w('w'):'90',w('type'):'dxa'})
        grid=SubElement(table,w('tblGrid')); widths=[440,3500,860,600,1460,1520,1440]
        for width in widths: SubElement(grid,w('gridCol'),{w('w'):str(width)})
        for number,row in enumerate(_rows(offer)):
            tr=SubElement(table,w('tr'))
            row_props=SubElement(tr,w('trPr'))
            SubElement(row_props,w('cantSplit'))
            if not number: SubElement(row_props,w('tblHeader'))
            for width,value in zip(widths,row):
                tc=SubElement(tr,w('tc')); tc_props=SubElement(tc,w('tcPr'))
                SubElement(tc_props,w('tcW'),{w('w'):str(width),w('type'):'dxa'})
                if not number: SubElement(tc_props,w('shd'),{w('fill'):'E7ECEF'})
                paragraph(tc,value,'TableText')
        paragraph(body,'')
        for note in _notes(offer): paragraph(body,note)
        sect=SubElement(body,w('sectPr')); SubElement(sect,w('pgSz'),{w('w'):'11906',w('h'):'16838'})
        SubElement(sect,w('pgMar'),{w('top'):'680',w('bottom'):'680',w('left'):'640',w('right'):'640'})
        styles=Element(w('styles'))
        for name,size,bold in [('Normal','20',False),('Title','36',True),('Heading1','28',True),('TableText','17',False)]:
            style=SubElement(styles,w('style'),{w('type'):'paragraph',w('styleId'):name})
            if name=='Normal': style.set(w('default'),'1')
            SubElement(style,w('name'),{w('val'):name})
            pp=SubElement(style,w('pPr')); SubElement(pp,w('spacing'),{w('after'):'100',w('line'):'260',w('lineRule'):'auto'})
            rp=SubElement(style,w('rPr')); SubElement(rp,w('rFonts'),{w('ascii'):'Arial',w('hAnsi'):'Arial',w('cs'):'Arial'})
            SubElement(rp,w('sz'),{w('val'):size}); SubElement(rp,w('color'),{w('val'):'000000'})
            if bold: SubElement(rp,w('b'))
        output=BytesIO()
        with ZipFile(output,'w',ZIP_DEFLATED) as archive:
            def write_part(name,data):
                # Retries of one immutable offer must produce the same artifact hash.
                info=ZipInfo(name,date_time=(1980,1,1,0,0,0))
                info.create_system=0; info.external_attr=0o600<<16
                archive.writestr(info,data,compress_type=ZIP_DEFLATED,compresslevel=9)
            write_part('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
            write_part('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            write_part('word/_rels/document.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
            write_part('word/document.xml',tostring(root,encoding='utf-8',xml_declaration=True))
            write_part('word/styles.xml',tostring(styles,encoding='utf-8',xml_declaration=True))
        return output.getvalue()

