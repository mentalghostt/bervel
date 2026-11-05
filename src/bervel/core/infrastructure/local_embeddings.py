"""Pinned multilingual E5 via CPU ONNX; no document content leaves the machine."""
from pathlib import Path
from threading import Lock

from bervel.core.errors import CoreError

MODEL='intfloat/multilingual-e5-small'
REVISION='614241f622f53c4eeff9890bdc4f31cfecc418b3'


class LocalEmbeddings:
    dimensions=384
    max_tokens=512
    space=MODEL+'@'+REVISION[:12]+':mean:384'

    def __init__(self,cache:Path,*,local_files_only:bool=False):
        self.cache=cache
        self.local_files_only=local_files_only
        self.engine=None
        self.lock=Lock()

    def load(self):
        with self.lock:
            if self.engine is not None: return
            try:
                from huggingface_hub import snapshot_download
                import onnxruntime
                onnxruntime.disable_telemetry_events()
                from tokenizers import Tokenizer
                from fastembed import TextEmbedding
                from fastembed.common.model_description import PoolingType, ModelSource
                path=snapshot_download(MODEL,revision=REVISION,cache_dir=str(self.cache/'hub'),
                    allow_patterns=['config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json','sentencepiece.bpe.model','onnx/model.onnx'],max_workers=2,
                    local_files_only=self.local_files_only)
                if not any(m['model']==MODEL for m in TextEmbedding.list_supported_models()):
                    TextEmbedding.add_custom_model(model=MODEL,pooling=PoolingType.MEAN,normalization=True,
                        sources=ModelSource(hf=MODEL),dim=self.dimensions,model_file='onnx/model.onnx')
                self.tokenizer=Tokenizer.from_file(str(Path(path)/'tokenizer.json'))
                self.tokenizer.no_truncation(); self.tokenizer.no_padding()
                self.engine=TextEmbedding(model_name=MODEL,specific_model_path=path,cache_dir=str(self.cache),
                    local_files_only=True,threads=2,providers=['CPUExecutionProvider'])
            except Exception:
                raise CoreError('EMBEDDING_UNAVAILABLE','Не удалось загрузить локальную модель эмбеддингов. Проверьте доступность её файлов и свободную память; оригиналы сохранены.') from None

    def count_tokens(self,value:str,*,purpose:str='document')->int:
        self.load()
        prefix='query: ' if purpose=='query' else 'passage: '
        return len(self.tokenizer.encode(prefix+value).ids)

    def embed(self,texts:list[str],*,purpose:str='document')->list[list[float]]:
        self.load()
        prefix='query: ' if purpose=='query' else 'passage: '
        inputs=[prefix+t for t in texts]
        if not inputs or any(len(self.tokenizer.encode(t).ids)>self.max_tokens for t in inputs):
            raise CoreError('KNOWLEDGE_CONTEXT_LIMIT','Текст превышает контекст локальной embedding-модели. Обрезание не выполнялось.')
        try:
            return [value.tolist() for value in self.engine.embed(inputs,batch_size=16)]
        except Exception:
            raise CoreError('EMBEDDING_UNAVAILABLE','Локальная модель не смогла рассчитать эмбеддинги. Готовые порции сохранены.') from None
