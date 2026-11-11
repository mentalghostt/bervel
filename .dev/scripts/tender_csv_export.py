"""Convert artifact-authored matrices to requested CSV, keeping literal text/IDs."""
from pathlib import Path
import csv
from datetime import datetime,timezone
import openpyxl
root=Path(__file__).resolve().parents[2]
folder=root/'.dev/local/tender-radar/authoring'
output=root/'.dev/outputs/tender-radar'
output.mkdir(parents=True,exist_ok=True)
for name in ('bervel-product-profile','kontur-public-tenders-2026-10-22'):
    book=openpyxl.load_workbook(folder/(name+'.xlsx'),read_only=True,data_only=True)
    with (output/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream)
        for row in book.active.iter_rows(values_only=True):
            # Artifact export represents aware ISO timestamps as UTC Excel datetimes.
            # Restore the timezone explicitly; never export ambiguous naive datetimes.
            writer.writerow(['' if cell is None else cell.replace(tzinfo=timezone.utc).isoformat() if isinstance(cell,datetime) and cell.tzinfo is None
                else cell.isoformat() if isinstance(cell,datetime) else str(cell).lower() if isinstance(cell,bool) else cell for cell in row])
    book.close()
    print(name+'.csv')
