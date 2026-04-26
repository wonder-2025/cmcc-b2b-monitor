#!/usr/bin/env python3
"""中国移动B2B采购网 — 公告详情解析（PDF提取中标厂商/候选人/最高限价预算）"""

import subprocess, json, base64, tempfile, os, re
from pypdf import PdfReader

DETAIL_URL = 'https://b2b.10086.cn/api-b2b/api-sync-es/white_list_api/b2b/publish/queryDetail'
HEADERS = [
    '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    '-H', 'Content-Type: application/json',
    '-H', 'Accept: application/json',
    '-H', 'Referer: https://b2b.10086.cn/',
]


def fetch_detail(publish_id, publish_uuid, notice_type='SELECTION_RESULTS'):
    payload = json.dumps({
        'publishId': publish_id, 'publishUuid': publish_uuid,
        'publishType': 'PROCUREMENT', 'publishOneType': notice_type,
    })
    for _ in range(3):
        try:
            r = subprocess.run(['curl','-sX','POST',DETAIL_URL]+HEADERS+['-d',payload],capture_output=True,text=True,timeout=60)
            d = json.loads(r.stdout)
            if d.get('code')==0: return d.get('data',{})
        except: pass
    return {}


def normalize(text):
    """合并行内换行（pypdf把公司名/关键词拆到两行）"""
    return re.sub(r'(?<!\n)\n(?!\n)', '', text)


def extract_text(pdf_b64):
    if not pdf_b64: return ''
    try:
        pdf = base64.b64decode(pdf_b64)
        with tempfile.NamedTemporaryFile(suffix='.pdf',delete=False) as f:
            f.write(pdf); p=f.name
        text = ''.join(pg.extract_text()+'\n' for pg in PdfReader(p).pages)
        os.unlink(p)
        return normalize(text)
    except: return ''


def extract_vendors(text):
    """中选结果 → 中标厂商（按标包）"""
    results = []
    for m in re.finditer(r'标包(\d+)(?:-[^\s：:]+)?(?:的)?(?:中标|中选|中选/成交|成交)人[：:\s]*(?:\d+[\.、]?\s*)?([^\n。，标包]+)', text):
        pkg, v = m.group(1), re.split(r'采购人|招标代理|标包|20\d{2}', m.group(2))[0].strip().rstrip('。，,. ')
        if v: results.append(f"标包{pkg}: {v}")
    if not results:
        for m in re.finditer(r'(?:中标|中选|中选/成交|成交)人[：:\s]*(?:\d+[\.、]?\s*)?([^\n。，]+)', text):
            v = re.split(r'采购人|招标代理|20\d{2}', m.group(1))[0].strip().rstrip('。，,. ')
            if v and 3<len(v)<80: results.append(v)
    return results or ['-']


def extract_candidates(text):
    """候选人公示 → 候选人列表（支持多种格式）"""
    results = []
    
    # 格式1: 标包N第一名 公司名...
    for m in re.finditer(r'标包(\d+)(?:名称)?\s*第一名\s*([^\d\n]{4,60}?)(?:未含税|不含税|含税|\d|$)', text):
        pkg, name = m.group(1), m.group(2).strip()
        if name and len(name) > 3:
            results.append(f"标包{pkg}: {name}")
    
    # 格式2: 第一名 公司名...（无标包号）
    if not results:
        for m in re.finditer(r'第一名\s*([^\d\n]{4,60}?)(?:未含税|不含税|含税|\d|$)', text):
            name = m.group(1).strip()
            if name and len(name) > 3:
                results.append(name)
    
    # 格式3: 原有格式
    if not results:
        for m in re.finditer(r'标包(\d+)(?:名称)?\s*(?:第[一二三四五六七八九十1234567890]+名|[1-9]\.)\s*([^\d\n]{4,40}?)(?:\s+\d)', text):
            results.append(f"标包{m.group(1)}: {m.group(2).strip()}")
    
    # 格式4: 无标包号
    if not results:
        for m in re.finditer(r'(?:第[一二三四五六七八九十1234567890]+名|[1-9]\.)\s*([^\d\n]{4,40}?)(?:\s+\d)', text):
            n = m.group(1).strip()
            if n not in results:
                results.append(n)
    
    return results[:5] or ['-']


def extract_price(text):
    """采购公告 → 最高限价/预算（优先提取具体数字）"""
    patterns = [
        r'(?:最高限价|最高投标限价|最高应答限价|控制价|拦标价)[：:\s]*(?:为|是)?\s*(\d[\d,\.]+)\s*(万元|元|万)',
        r'预算金额[：:\s]*(?:为|是)?\s*(\d[\d,\.]+)\s*(万元|元|万)',
        r'(?:采购|项目)预算[：:\s]*(?:为|是)?\s*(\d[\d,\.]+)\s*(万元|元|万)',
        r'总价(\d[\d,\.]+)\s*(万元|元|万)',
        r'(\d[\d,\.]+)\s*(万元|元|万)\s*(?:（.*?）)?(?:为|作为)?(?:最高|预算)',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return f"{m.group(1)}{m.group(2)}"
    # 兜底：提取任何金额
    m = re.search(r'(\d[\d,\.]+)\s*(万元|元|万)', text)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return '-'


def make_link(item_id, item_uuid, nt='SELECTION_RESULTS'):
    return f"https://b2b.10086.cn/#/noticeDetail?publishId={item_id}&publishUuid={item_uuid}&publishType=PROCUREMENT&publishOneType={nt}"
