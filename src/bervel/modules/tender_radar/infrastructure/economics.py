"""Lossless economic CSV adapter; empty numeric values remain unknown."""
from pathlib import PurePath
import re
from bervel.core.csv_memory import read_csv
from bervel.core.errors import CoreError
from bervel.modules.tender_radar.stock import number,decimal_text,unit

NUMBERS=('sale_price','unit_cost','fixed_cost','logistics_cost','packaging_per_unit','capacity_per_day','production_lead_days','delivery_lead_days')

class EconomicsImporter:
    def parse(self,content:bytes,filename:str)->dict:
        try:
            if PurePath(filename).suffix.casefold()!='.csv':raise ValueError('Нужен CSV экономики.')
            header,rows,_,truncated=read_csv(content)
            fields=[h.strip().casefold() for h in header]
            if truncated or len(fields)!=len(set(fields)) or not set(('sku','unit','currency','data_kind')+NUMBERS)<=set(fields):
                raise ValueError('Нужен полный CSV с уникальными колонками экономики/производительности.')
            result=[];seen=set();kinds=set()
            for rownum,values in enumerate(rows,2):
                if not any(v.strip() for v in values):continue
                if len(values)!=len(fields):raise ValueError('Не совпадает число колонок.')
                row=dict(zip(fields,(v.strip() for v in values)))
                if not row['sku'] or len(row['sku'])>100 or row['sku'] in seen:raise ValueError('Пустой/повторный SKU.')
                if not re.fullmatch('[A-Z]{3}',row['currency']):raise ValueError('Нужна валюта ISO из трёх заглавных букв.')
                measure=unit(row['unit'])
                if measure is None:raise ValueError('Неизвестная единица.')
                if row['data_kind'] not in {'mock','reported'}:raise ValueError('Нужен data_kind mock/reported.')
                parsed={k:decimal_text(number(row[k])) if row[k] else None for k in NUMBERS}
                result.append(dict(sku=row['sku'],unit=measure,currency=row['currency'],row=rownum,**parsed))
                seen.add(row['sku']);kinds.add(row['data_kind'])
            if not result or len(kinds)!=1:raise ValueError('Нужны непустые однородные данные; mock/reported не смешиваются.')
            return {'kind':kinds.pop(),'items':result}
        except (ValueError,CoreError) as error:
            raise CoreError('RADAR_ECONOMICS_INVALID','Не удалось прочитать экономику. '+(error.message if isinstance(error,CoreError) else str(error))) from None
