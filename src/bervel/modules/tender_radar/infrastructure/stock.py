"""Strict stock CSV adapter over the shared lossless CSV decoder."""
from datetime import datetime
from pathlib import PurePath

from bervel.core.csv_memory import read_csv
from bervel.core.errors import CoreError
from bervel.modules.tender_radar.models import StockData
from bervel.modules.tender_radar.stock import number,decimal_text,unit


class StockImporter:
    def parse(self,content:bytes,filename:str)->StockData:
        try:
            if PurePath(filename).suffix.casefold()!='.csv':raise ValueError('Нужен CSV свободных остатков.')
            header,rows,_,truncated=read_csv(content)
            if truncated:raise ValueError('Ведомость превышает предел строк; частичные остатки не используются.')
            fields=[h.strip().casefold() for h in header]
            if len(fields)!=len(set(fields)) or not {'sku','product','free_quantity','unit'}<=set(fields):
                raise ValueError('Нужны уникальные колонки sku,product,free_quantity,unit. Количество заявки не является свободным остатком.')
            items=[];seen=set();kinds=set()
            for row_number,values in enumerate(rows,2):
                if not any(v.strip() for v in values):continue
                if len(values)!=len(fields):raise ValueError(f'Не совпадает число ячеек, строка {row_number}.')
                row=dict(zip(fields,(v.strip() for v in values)))
                sku,product=row['sku'],row['product']
                if not sku or len(sku)>100 or not product or len(product)>1000 or sku in seen:
                    raise ValueError(f'Пустой/повторный SKU или неверное название, строка {row_number}.')
                measure=unit(row['unit'])
                if measure is None:raise ValueError(f'Неизвестная единица, строка {row_number}.')
                count=number(row['free_quantity']) if row['free_quantity'] else None
                if count is not None and measure in {'шт','комплект','упаковка'} and count!=count.to_integral_value():
                    raise ValueError(f'Дробное число целых изделий, строка {row_number}.')
                kind=row.get('data_kind') or 'reported'
                if kind not in {'mock','reported'}:raise ValueError('data_kind должен быть mock или reported.')
                as_of=row.get('as_of') or None
                if as_of and datetime.fromisoformat(as_of).utcoffset() is None:raise ValueError('as_of требует часовой пояс.')
                specs={key:row.get(key,'') for key in ('standard','thread','length_mm','strength','coating')}
                if any(len(value)>500 for value in specs.values()):raise ValueError('Слишком длинные характеристики.')
                items.append(dict(sku=sku,product=product,free_quantity=decimal_text(count) if count is not None else None,
                    unit=measure,specs=specs,row=row_number,as_of=as_of))
                seen.add(sku);kinds.add(kind)
            if not items or len(kinds)!=1:raise ValueError('Нужна непустая однородная ведомость; мок и заявленные остатки не смешиваются.')
            return dict(kind=kinds.pop(),items=items)
        except (ValueError,CoreError) as error:
            message=error.message if isinstance(error,CoreError) else str(error)
            raise CoreError('RADAR_STOCK_INVALID','Не удалось прочитать свободные остатки. '+message) from None
