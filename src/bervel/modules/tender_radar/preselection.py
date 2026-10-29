"""Cheap high-recall screening; uncertainty always reaches semantic assessment."""
import re
import json

from bervel.modules.tender_radar.analysis import eligibility

FASTENERS=('болт','гайк','шайб','винт','шпильк','крепеж','метиз','bolt','nut','washer','screw','stud','fastener')
AMBIGUOUS=('метиз','крепеж','креплен','детал','издел','комплект','оборудован','материал','запчаст')
OUTSIDE=('кабел','динамик','колонк','обув','одежд','футбол','мяч','медикамент','молок','бензин','cable','speaker')


def contains(text,stems):
    value=text.casefold().replace('ё','е')
    return any(re.search(r'\b'+re.escape(stem),value) for stem in stems)


def fastener_only(sources):
    """Only structured, wholly fastener product columns justify a hard names gate.

    Free text can name another product with a synonym that our cheap dictionary
    does not know. Such profiles always go to semantic assessment.
    """
    products=[]
    try:
        for source in sources:
            lines=source['excerpt'].splitlines()
            if not lines or not lines[0].startswith('Columns: '):return False
            columns=json.loads(lines[0][9:])
            index=[c.strip().casefold() for c in columns].index('product')
            for line in lines[1:]:
                if not line.strip():continue
                match=re.fullmatch(r'Row \d+: (.+)',line)
                if not match:return False
                values=json.loads(match[1]);product=values[index]
                if not isinstance(product,str) or not contains(product,FASTENERS):return False
                if re.search(r'[,;|/]|\b(?:и|and)\b',product.casefold()):return False
                if not any(re.match(re.escape(stem),product.casefold().replace('ё','е')) for stem in FASTENERS):return False
                products.append(product)
    except (ValueError,TypeError,KeyError,IndexError):return False
    return bool(products)


def preselect(rows,sources,now):
    profile='\n'.join(source['excerpt'] for source in sources)
    fastener_profile=fastener_only(sources)
    selected=[];excluded={}
    for row in rows:
        decision,reason=eligibility(row,now)
        if decision=='excluded':
            excluded[row['id']]={'stage':'eligibility','reason':reason};continue
        description=row.get('title','')+'\n'+row.get('description','')
        # Positive descriptions and broad/unknown names must survive this gate.
        outside=[stem for stem in OUTSIDE if contains(description,(stem,))]
        if fastener_profile and not contains(description,FASTENERS+AMBIGUOUS) and outside and not any(contains(profile,(stem,)) for stem in outside):
            excluded[row['id']]={'stage':'names','reason':'Название и описание явно указывают на непрофильный товар для выбранного крепёжного профиля.'}
        else:selected.append(row)
    return selected,excluded
