"""Fail-closed diagnostic disk boundary; never a business-state mutator.

Only fixed product keys and code values survive. Free exception text, source
lines, window titles, account labels and arbitrary nested keys do not. UI may
retain original problems in memory; persistence/export always uses this copy.
"""
import math
import re
from diagnostic_schema import FIELDS, CODES, MODULES, EXCEPTIONS

PRIVATE = re.compile(r'username|nickname|playername|account|password|secret|tokenvalue|(?:window|process|program)(?:name|title|exe|path)|foreground(hwnd|pid|name)|executable|commandline|hostname',re.I)
FREE = frozenset(('raw','summary','title','message','error','errorMessage','details','stateGetterError'))
STACK = frozenset(('traceback','stack'))
EXCEPTION = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Unavailable))\b')
HEX = re.compile(r'[a-fA-F0-9]{16,64}\Z')
CLOCK = re.compile(r'\d{4}-\d{2}-\d{2}T[0-9:.+Z-]{8,32}\Z')


def exception_text(text):
    kinds=list(dict.fromkeys(kind for kind in EXCEPTION.findall(text) if kind in EXCEPTIONS))[:8]
    return ': '.join(kinds) + (': <redacted-message>' if kinds else '<redacted-text>')


def stack_text(text):
    rows=[]
    # Match the final quote/line suffix greedily on each physical frame line;
    # apostrophes and misleading ", line N" fragments are legal inside paths.
    for line in text.split('\n'):
        match=re.search(r"File\s+[\"'](.*)[\"'],\s*line\s+(\d+)(?:,\s*in\s+(.*))?$",line.strip())
        if not match:continue
        module=match[1].replace('\\','/').split('/')[-1]
        module=module if module in MODULES else '<external-module>'
        name=(match[3] or '').strip("'\"")
        function=name if name in CODES else '<function>'
        rows.append(f'File "{module}", line {match[2]}, in {function}')
    kinds=exception_text(text)
    return '\n'.join(rows[-12:]+[kinds])


def safe_record(value):
    budget=[2048]
    def visit(item,key='',depth=0):
        budget[0]-=1
        if budget[0]<0 or depth>10:return None
        if item is None or type(item) in (bool,int):return item
        if type(item) is float:return item if math.isfinite(item) else None
        if isinstance(item,str):
            if key in STACK:return stack_text(item[:32768])
            if key in FREE:return exception_text(item[:16384])
            if item in CODES or HEX.fullmatch(item) or CLOCK.fullmatch(item):return item
            if key in ('invalidationOrigin','exceptionOrigin'):
                parts=item.split(':')
                if all(part in CODES or part in EXCEPTIONS for part in parts):return item
            if key == 'logStream' and depth == 1 and re.fullmatch(r'(?:events-[A-Za-z0-9_.-]+\.jsonl|lifecycle-detail\.jsonl)',item):return item
            if key == 'installIdStatus' and item.startswith('unavailable:') and item.split(':',1)[1] in EXCEPTIONS:return item
            if key in ('buildFingerprint','runtimeBuildFingerprint') and re.fullmatch(r'[A-Z0-9]{1,64}',item):return item
            if key == 'exceptionType' and item in EXCEPTIONS:return item
            if key in ('mapId','floor','id','seq','count') and re.fullmatch(r'[0-9]{1,20}',item):return item
            if key in ('occurrenceId',) and re.fullmatch(r'events-[A-Za-z0-9_.:-]{1,160}',item):return item
            return '<redacted-text>'
        if isinstance(item,(list,tuple)):
            return [visit(v,key,depth+1) for v in item[:128]]
        if isinstance(item,dict):
            if item.get('role')=='other':return {'role':'other'}
            result={}
            for k,v in list(item.items())[:256]:
                if not isinstance(k,str) or PRIVATE.search(k) or k in ('name','path','filePath','localPath','imageSource','url'):continue
                if k not in FIELDS and not (key == 'suppressionReasons' and k in CODES):continue
                # PID/HWND belongs only to our process envelope or explicitly
                # identified Qt/bound-game window; no other-program identities.
                if k in ('pid','processId','hwnd') and depth and item.get('role') not in ('qt','bound-game'):continue
                result[k]=visit(v,k,depth+1)
            return result
        return None
    return visit(value)


def sanitize_log_payload(payload):
    """Protect legacy exported logs too, without rewriting their source files."""
    import json
    text=payload.decode('utf-8',errors='replace')
    try:
        return json.dumps(safe_record(json.loads(text)),ensure_ascii=False,separators=(',',':')).encode('utf-8')
    except (ValueError,TypeError):
        lines=[]
        for line in text.split('\n'):
            if not line.strip():continue
            try:value=safe_record(json.loads(line))
            except (ValueError,TypeError):value=dict(event='legacy-text-redacted',traceback=stack_text(line))
            lines.append(json.dumps(value,ensure_ascii=False,separators=(',',':')))
        return ('\n'.join(lines)+'\n').encode('utf-8') if lines else b''
