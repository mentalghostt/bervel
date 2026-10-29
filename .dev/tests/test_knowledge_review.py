"""Regressions independently found in the indexed path, not the legacy search."""
import io
from uuid import UUID,uuid4

import pytest

from bervel.core.document_memory import DocumentMemory
from bervel.core.errors import CoreError
from bervel.core.models import RegisterArtifact
from test_knowledge import setup,upload,Vectors


def test_complete_profile_rejects_missing_archived_selected_revision(services,pg_engine):
    service,repo,_,artifacts,actor=setup(services,pg_engine)
    first=upload(artifacts,actor,'Болт A'); second=upload(artifacts,actor,'Болт B')
    while service.run_once(): pass
    catalog=[artifacts.metadata(actor,r) for r in (first,second)]
    artifacts.delete(actor,second.artifact_id)
    with pytest.raises(CoreError) as error:
        DocumentMemory(artifacts,repo,service.reader,knowledge=service).context(actor,catalog,'',complete=True)
    assert error.value.code=='ARTIFACT_DELETED'


def test_every_long_xlsx_cell_fragment_retains_sheet_product_and_column():
    import openpyxl
    from bervel.core.infrastructure.document_reader import DocumentReader
    book=openpyxl.Workbook(); sheet=book.active; sheet.title='Products'
    sheet.append(['Product','Delivery conditions'])
    sheet.append(['Болт М16',('Только доставка морем. '*260)+'END_ONLY_FOB'])
    output=io.BytesIO();book.save(output)
    chunks=DocumentReader().extract(output.getvalue(),'parts.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')['chunks']
    parts=[c for c in chunks if c['row_start']==2 and ('доставка' in c['excerpt'] or 'END_ONLY_FOB' in c['excerpt'])]
    assert len(parts)>1
    assert all('Products' in c['excerpt'] and 'Болт М16' in c['excerpt'] and 'Delivery conditions' in c['excerpt'] for c in parts)


def test_indexed_comparison_keeps_small_explicitly_named_file(services,pg_engine,monkeypatch):
    import bervel.core.infrastructure.repository as storage
    ids=iter([UUID('00000000-0000-4000-8000-000000000001'),UUID('00000000-0000-4000-8000-000000000002')])
    monkeypatch.setattr(storage,'uuid4',lambda:next(ids))
    service,repo,_,artifacts,actor=setup(services,pg_engine)
    alpha=artifacts.register(actor,RegisterArtifact(('product,quantity\n'+''.join(f'Болт,{i}\n' for i in range(80))).encode(),'alpha.csv','text/csv',{},'alpha'))
    beta=artifacts.register(actor,RegisterArtifact('product,quantity\nБолт,83\n'.encode(),'beta.csv','text/csv',{},'beta'))
    while service.run_once(): pass
    sources=DocumentMemory(artifacts,repo,service.reader,knowledge=service).context(actor,[artifacts.metadata(actor,r) for r in (alpha,beta)],'Сравни alpha.csv и beta.csv')
    assert {str(alpha.artifact_id),str(beta.artifact_id)} <= {s['artifact_id'] for s in sources}


def test_valid_long_chat_question_is_searched_without_truncation(services,pg_engine):
    service,repo,_,artifacts,actor=setup(services,pg_engine)
    class ShortVectors(Vectors):
        max_tokens=32
        def count_tokens(self,value): return len(value.split())
        def embed(self,texts,**kwargs):
            if any(self.count_tokens(t)>32 for t in texts): raise CoreError('KNOWLEDGE_CONTEXT_LIMIT','too long')
            return super().embed(texts,**kwargs)
    service.embeddings=ShortVectors()
    ref=upload(artifacts,actor)
    while service.run_once(): pass
    sources=service.retrieve(actor,[artifacts.metadata(actor,ref)],'болт '*600)
    assert sources and any('Болт М16' in s['excerpt'] for s in sources)
