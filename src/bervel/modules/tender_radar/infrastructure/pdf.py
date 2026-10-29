"""Fixed BERVEL report template. Model/file values are text, never layout or markup."""
from datetime import datetime
from io import BytesIO
import json
import re
from urllib.parse import urlsplit
from xml.sax.saxutils import escape, quoteattr
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle

TEMPLATE_VERSION = '1.3'
INK = colors.HexColor('#202e29')
MUTED = colors.HexColor('#626d67')
ACCENT = colors.HexColor('#176650')
LINE = colors.HexColor('#dde3dc')
PALE = colors.HexColor('#edf5ee')
LABELS = {'take_in_work':'В работу сейчас', 'needs_review':'Уточнить', 'excluded':'Исключено', 'not_analysed':'Вне анализа'}
from bervel.core.infrastructure.pdf_fonts import register_pdf_fonts as fonts


def plain(value):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value or '')).replace('\u2011','-')


def markup(value):
    return escape(plain(value)).replace('\n','<br/>')


def date(value):
    if not value: return 'Неизвестно'
    return datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M МСК')


def provenance(value):
    try:
        parsed = json.loads(value)
        if isinstance(parsed,list) and all(isinstance(p,dict) and isinstance(p.get('note'),str) for p in parsed):
            return '\n'.join(p['note'] for p in parsed)
    except (ValueError,TypeError): pass
    return value


def render_radar_pdf(run: dict) -> bytes:
    fonts()
    report = run['report']
    full=report.get('schema_version')=='1.3.0'
    labels={'take_in_work':'Можно участвовать','needs_review':'Требует внимания','excluded':'Вне анализа'} if full else LABELS
    out = BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
        topMargin=23*mm, bottomMargin=20*mm, title='BERVEL - Тендерный радар', author='BERVEL',
        creator='BERVEL report template '+TEMPLATE_VERSION, invariant=1, pageCompression=1)
    body = ParagraphStyle('body', fontName='Bervel', fontSize=9, leading=13.5, textColor=INK,
        alignment=TA_LEFT, spaceAfter=6, splitLongWords=True, allowWidows=0, allowOrphans=0)
    small = ParagraphStyle('small', parent=body, fontSize=7.5, leading=11, textColor=MUTED)
    title = ParagraphStyle('title', parent=body, fontName='BervelBold', fontSize=25, leading=31, textColor=ACCENT, spaceAfter=12)
    heading = ParagraphStyle('heading', parent=body, fontName='BervelBold', fontSize=15, leading=20, textColor=ACCENT, spaceBefore=12, spaceAfter=9, keepWithNext=True)
    item_heading = ParagraphStyle('item', parent=body, fontName='BervelBold', fontSize=10, leading=14, spaceBefore=9, keepWithNext=True)
    story = []
    def p(value, style=body):
        story.append(Paragraph(markup(value), style))
    def link(url):
        value=plain(url)
        parsed=urlsplit(value)
        safe=parsed.scheme in {'http','https'} and parsed.hostname and not parsed.username and not parsed.password
        text=markup(value)
        story.append(Paragraph(f'<link href={quoteattr(value)} color="#176650">{text}</link>' if safe else text,small))

    p('Тендерный радар', title)
    p('ОТЧЁТ ПО СКЛАДУ И ЭКОНОМИКЕ' if full else 'ОТЧЁТ О ПЕРВИЧНОМ ОТБОРЕ', small)
    if not full:p('В работу сейчас — первичный кандидат; экономические проверки не выполнялись.',small)
    if full and not report.get('analysis_complete'):p('АНАЛИЗ НЕ ЗАВЕРШЁН. Отчёт содержит непроверенные строки.',item_heading)
    p('Состояние на: '+date(report['as_of']))
    p('Источник закупок: '+report['batch']['filename'])
    if report.get('stock_source'):
        source=report['stock_source']
        p(f"Свободный остаток: {source['filename']}")
        p('Допустимая нехватка: '+report.get('selection',{}).get('max_shortage_percent','0')+'%')
    if report.get('stock_kind') == 'mock':
        p('МОК: складские количества являются тестовыми и не подтверждают реальные остатки.',item_heading)
    p('Модель: '+(run.get('model') or 'Первичные проверки без модели'),small)
    if run.get('focus'): p('Фокус: '+run['focus'])
    p(report['notice'])
    if not full:p('Техническая и производственная готовность, складские остатки, цена БЕРВЕЛ и маржа не подтверждены. Участие не одобрено.',small)
    if report.get('economics_source'):
        source=report['economics_source'];p(f"Экономика: {source['filename']}")
        p('Порог маржи: '+str(report.get('selection',{}).get('filters',{}).get('min_margin_percent','15'))+'%')
    if report.get('economics_kind')=='mock':p('МОК: цены, затраты и мощности демонстрационные.',item_heading)
    if report.get('coverage'):
        c=report['coverage'];p(f"Учтено строк: {c['accounted_rows']} из {c['input_rows']}; дубли: {c['duplicate_rows']}; пропущено пустых/итоговых: {c['skipped_rows']}.",small)
    table_rows=[[Paragraph('Категория',body),Paragraph('Закупок',body)]]
    table_rows += [[Paragraph(label,body),str(report['counts'][key]+(report['counts']['not_analysed'] if full and key=='excluded' else 0))] for key,label in labels.items()]
    table=Table(table_rows,colWidths=[doc.width-30*mm,30*mm],hAlign='LEFT')
    table.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),'Bervel'),('FONTSIZE',(0,0),(-1,-1),9),
        ('TEXTCOLOR',(0,0),(-1,-1),INK),('BACKGROUND',(0,0),(-1,0),PALE),('LINEBELOW',(0,0),(-1,-1),0.5,LINE),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),
        ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
    story += [Spacer(1,5*mm),table,Spacer(1,4*mm)]
    p(f"Всего: {report['counts']['total']}. Модель рассмотрела: {report['counts']['model_analysed']}.",small)
    p('Выбранные файлы',heading)
    for source in run['sources_snapshot']:
        p(f"{source['filename']}")
    for decision,label in labels.items():
        story.append(PageBreak())
        p(label,heading)
        items=[item for item in report['items'] if item['decision']==decision or (full and decision=='excluded' and item['decision']=='not_analysed')]
        p('Закупок: '+str(len(items)),small)
        if not items: p('В этой категории закупок нет.')
        for item in items:
            p('№ '+item['number']+' · '+item['title'],item_heading)
            amount=str(item['price']).replace('.',',')+' '+item['currency'] if item.get('price') is not None else 'Не указана'
            p('Цена: '+amount+' | Подать до: '+date(item.get('deadline_at')),small)
            p('Стадия проверена: '+date(item.get('source_observed_at')),small)
            link(item['source_url'])
            p(item['reason'])
            if item.get('meaning_reason') and item['meaning_reason'] != item['reason']:
                p('Оценка модели: '+item['meaning_reason'])
            if item.get('fit'):
                fit_labels={'relevant':'Соответствует профилю','uncertain':'Недостаточно данных','irrelevant':'Вне профиля'}
                p('Товарное соответствие: '+fit_labels[item['fit']])
            if item.get('product_group'):
                p('Группа: '+item['product_group'])
                p('Из тендера: '+item['tender_quote'])
                p('Из профиля: '+item['profile_quote'])
            check=item.get('stock_check')
            if check:
                statuses={'pass':'Допуск соблюдён','insufficient':'Нехватка выше допуска','unknown':'Требует уточнения'}
                p('Свободный остаток · '+statuses[check['status']],item_heading)
                for line in check['checks']:
                    p(f"{line['sku']} · {line['product']}")
                    p(f"Нужно: {line['required']} {line['unit']} | Свободно: {line['available']} {line['unit']}")
                    p(f"Нехватка: {line['shortage']} {line['unit']} ({line['shortage_percent']}%) · строка ведомости {line['source_row']}",small)
                for reason in check['reasons']: p(reason,small)
            if item.get('demo'): p('Учебный сценарий.',small)
            economy=item.get('economics_check')
            if economy:
                p('Экономика · '+{'pass':'Проверена по выбранным данным','fail':'Ограничения не соблюдены','unknown':'Недостаточно данных'}[economy['status']],item_heading)
                currency=economy.get('currency') or 'валюта неизвестна'
                for key,label_text in (('revenue','Выручка по ценам продажи'),('total_cost','Полные расходы'),('profit','Прибыль'),('budget','Бюджет закупки')):
                    p(label_text+': '+str(economy.get(key) or 'Неизвестно')+' '+currency)
                p('Маржа: '+str(economy.get('margin_percent') or 'Неизвестно')+'%')
                p('Необходимо дней: '+str(economy.get('required_days') or 'Неизвестно')+' | Срок поставки: '+str(economy.get('available_days') or 'Неизвестно'))
                for line in economy['lines']:
                    p(f"{line['sku']}: {line['quantity']} × {line['sale_price']} = {line['revenue']}; расходы {line['cost']}; строка экономики {line['source_row']}",small)
                for reason in economy['reasons']:p(reason,small)
            if item.get('origins'):p('Исходные строки: '+', '.join((str(o.get('sheet') or '')+' '+str(o['row'])).strip() for o in item['origins'] if o),small)
            if item.get('provenance'): p('Происхождение: '+provenance(item['provenance']),small)
            for risk in item['risks']: p('Проверить: '+risk)
            for step in item['next_steps']: p('Далее: '+step)
            indexes=item['evidence_indexes']
            if indexes: p('Доказательства: '+', '.join('['+str(index+1)+']' for index in indexes)+' — см. раздел источников.',small)
            story.append(Spacer(1,3*mm))
    story.append(PageBreak())
    p('Доказательства из выбранных файлов',heading)
    for index,source in enumerate(report['sources']):
        p(f"[{index+1}] {source['filename']}",item_heading)
        p(f"{source.get('unit') or 'row'} {source.get('row_start',0)}–{source.get('row_end',0)}",small)
        if source.get('truncated'): p('Выбранный фрагмент; контекст частичный.',small)
        p(source['excerpt'])
    if not report['sources']: p('Фрагменты источников отсутствуют.')

    def page(canvas, current):
        canvas.saveState()
        width,height=A4
        canvas.setStrokeColor(LINE); canvas.setLineWidth(0.5)
        canvas.line(18*mm,height-15*mm,width-18*mm,height-15*mm)
        canvas.setFont('BervelBold',9); canvas.setFillColor(ACCENT)
        canvas.drawString(18*mm,height-11*mm,'BERVEL / ТЕНДЕРНЫЙ РАДАР')
        canvas.setFont('Bervel',7); canvas.setFillColor(MUTED)
        canvas.drawString(18*mm,13*mm,'Шаблон '+TEMPLATE_VERSION+' · '+str(run['run_id']))
        canvas.drawRightString(width-18*mm,13*mm,'Страница '+str(current.page))
        canvas.restoreState()
    doc.build(story,onFirstPage=page,onLaterPages=page)
    return out.getvalue()
