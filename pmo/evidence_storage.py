"""Private originals. Callers must authorize both writes and reads."""
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import re
from zipfile import ZipFile

from defusedxml.ElementTree import fromstring
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import IndirectObject

from .identity import Failure

LIMIT=20*1024*1024
MIMES={'.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png'}


def inspect_image(data:bytes,kind:str):
    fmt='PNG' if kind=='image/png' else 'JPEG'
    for verify in (True,False):
        with Image.open(BytesIO(data),formats=[fmt]) as img:
            if img.width*img.height>16_000_000 or getattr(img,'n_frames',1)!=1: raise ValueError('Image bounds')
            if verify: img.verify()
            else: img.load()


def inspect_docx(data:bytes):
    with ZipFile(BytesIO(data)) as z:
        infos=z.infolist(); names={i.filename for i in infos}
        if len(infos)>1500 or len(names)!=len(infos) or sum(i.file_size for i in infos)>48*1024*1024: raise ValueError('Package bounds')
        if not {'[Content_Types].xml','_rels/.rels','word/document.xml'}<=names: raise ValueError('Word package required')
        for i in infos:
            path=PurePosixPath(i.filename)
            if i.flag_bits&1 or path.is_absolute() or '..' in path.parts or '\\' in i.filename or ':' in i.filename: raise ValueError('Invalid package path')
            if i.is_dir(): continue
            if i.file_size>16*1024*1024: raise ValueError('Part too large')
            part=z.read(i)
            if i.filename.endswith(('.xml','.rels')):
                node=fromstring(part,forbid_dtd=True)
                if i.filename=='word/document.xml' and node.tag!='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document': raise ValueError('Not a Word document')
                if i.filename=='[Content_Types].xml' and not any(e.get('PartName')=='/word/document.xml' and e.get('ContentType')=='application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml' for e in node): raise ValueError('Unexpected main part')
                if i.filename.endswith('.rels') and any(e.get('TargetMode','').casefold()=='external' for e in node): raise ValueError('External reference')
                if any(e.tag.rsplit('}',1)[-1] in ('object','oleObject','altChunk','instrText','fldSimple') for e in node.iter()): raise ValueError('Active content')
            elif path.suffix.lower() in ('.png','.jpg','.jpeg'): inspect_image(part,MIMES[path.suffix.lower()])
            else: raise ValueError('Unsupported package part')


def inspect_pdf(data:bytes):
    if not data.startswith(b'%PDF-') or not data.rstrip().endswith(b'%%EOF'): raise ValueError('Invalid PDF')
    reader=PdfReader(BytesIO(data),strict=True)
    if reader.is_encrypted or not 1<=len(reader.pages)<=500: raise ValueError('Unsupported PDF')
    pending=[reader.trailer]; seen:set[tuple[int,int]]=set(); count=0
    while pending:
        node=pending.pop(); count+=1
        if count>120000: raise ValueError('PDF bounds')
        if isinstance(node,IndirectObject):
            key=(node.idnum,node.generation)
            if key in seen: continue
            seen.add(key); pending.append(node.get_object())
        elif isinstance(node,dict):
            if set(node)&{'/OpenAction','/AA','/JS','/JavaScript','/EmbeddedFiles','/EF','/AcroForm','/Launch','/RichMediaContent','/XFA','/A'}: raise ValueError('Active PDF')
            pending.extend(node.values())
        elif isinstance(node,list): pending.extend(node)


def validate(name:str,mime:str,data:bytes,*,project_source=False):
    if not name or len(name)>255 or re.search(r'[\\/:\x00-\x1f\x7f]',name): raise Failure(422,'INVALID_FILENAME')
    if project_source:
        if Path(name).suffix.lower() != '.json' or mime != 'application/json': raise Failure(422,'UNSUPPORTED_EVIDENCE_FORMAT')
        if not 0 < len(data) <= 16384: raise Failure(422,'INVALID_EVIDENCE_SIZE')
        try:
            from .cost_reconciliation import _read_source
            _read_source(data)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise Failure(422,'INVALID_EVIDENCE_CONTENT') from exc
        return
    if mime!=MIMES.get(Path(name).suffix.lower()) or mime not in MIMES.values(): raise Failure(422,'UNSUPPORTED_EVIDENCE_FORMAT')
    if not 0<len(data)<=LIMIT: raise Failure(413 if data else 422,'INVALID_EVIDENCE_SIZE')
    try:
        if mime.startswith('image/'): inspect_image(data,mime)
        elif mime=='application/pdf': inspect_pdf(data)
        else: inspect_docx(data)
    except Exception as exc:
        raise Failure(422,'INVALID_EVIDENCE_CONTENT') from exc


def root()->Path:
    base=Path(os.getenv('PMO_EVIDENCE_DIR',str(Path(__file__).resolve().parents[1]/'.local'/'evidence'))).resolve()
    web=Path(__file__).resolve().parents[1]/'web'
    if base.is_relative_to(web): raise Failure(503,'PRIVATE_STORAGE_REQUIRED')
    return base


def store(data:bytes,key:str)->None:
    base=root()
    try:
        base.mkdir(mode=0o700,parents=True,exist_ok=True)
        with (base/key).open('xb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
    except OSError as exc: raise Failure(503,'EVIDENCE_STORAGE_UNAVAILABLE') from exc


def checked_bytes(key:str,digest:str,size:int)->bytes:
    if not re.fullmatch('[0-9a-f]{32}',key) or not 0<size<=LIMIT: raise Failure(409,'EVIDENCE_INTEGRITY_FAILED')
    path=root()/key
    try:
        if path.is_symlink() or path.resolve()!=path or path.stat().st_size!=size: raise Failure(409,'EVIDENCE_INTEGRITY_FAILED')
        with path.open('rb') as f: data=f.read(size+1)
    except OSError as exc: raise Failure(409,'EVIDENCE_INTEGRITY_FAILED') from exc
    if len(data)!=size or sha256(data).hexdigest()!=digest: raise Failure(409,'EVIDENCE_INTEGRITY_FAILED')
    return data
