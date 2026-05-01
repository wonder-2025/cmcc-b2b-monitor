#!/usr/bin/env python3
"""中国移动B2B采购网 — 每日标讯推送（近3天网络安全项目）"""

import subprocess, json, base64, tempfile, os, re, sys
from datetime import datetime, timedelta

# ===== 配置（通过环境变量） =====
WECOM_WEBHOOK = os.environ.get('CMCC_WEBHOOK', '')
SAVE_DIR = os.path.expanduser('~/标讯/移动')
STATE_FILE = os.environ.get('CMCC_STATE_FILE', '/tmp/cmcc_daily_state.json')

API_LIST = 'https://b2b.10086.cn/api-b2b/api-sync-es/white_list_api/b2b/publish/queryList'
API_DETAIL = 'https://b2b.10086.cn/api-b2b/api-sync-es/white_list_api/b2b/publish/queryDetail'
H = ['-H','User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
     '-H','Content-Type: application/json','-H','Accept: application/json','-H','Referer: https://b2b.10086.cn/']

SEC_KW = [
    '安全',
    '网安', '信安',
    '等保', '合规',
    '反诈', '涉诈',
    '漏洞', '攻防', '渗透', '渗透测试', '漏洞管理',
    '态势感知', '威胁情报', '威胁分析', '威胁', '暴露面',
    '防火墙', 'WAF', '入侵检测',
    '数据防泄漏',
    '加密',
    'DDOS', '抗D', '流量清洗',
    '僵木蠕',
    '个人信息保护',
]
EXC = ['视频监控','防雷','防雷检测','电力监控','碳减排','安全生产','碳中和','交通安全','道路交通',
       '地震安全','功能安全','内容安全','消防安全','电气安全','食品安全','施工安全',
       '反诈宣传', '燃气安全', '质量安全',
       '存货盘点合规审计', '合规审计支撑', '反贿赂', '管理体系认证']


def curl_post(url, payload, timeout=30):
    # Python 3.6兼容写法
    proc = subprocess.Popen(['curl','-sX','POST',url]+H+['-d',json.dumps(payload)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        if proc.returncode == 0:
            return json.loads(stdout.decode('utf-8'))
    except Exception as e:
        print(f"[curl_post错误] {e}", file=sys.stderr)
    return {}


def sanitize_filename(title, max_len=40):
    """生成安全的文件名"""
    s = re.sub(r'[\\/:*?"<>|\n\r\t]', '', title)
    s = s.strip().replace(' ', '_')
    return s[:max_len]


def save_to_md(item, detail_text, typ, idx, total):
    """保存单条标讯为 md 文件"""
    today = datetime.now().strftime("%Y-%m-%d")
    day_dir = os.path.join(SAVE_DIR, today)
    os.makedirs(day_dir, exist_ok=True)

    title = item.get('name', '无标题')
    unit = item.get('companyTypeName', '')
    ct = item.get('publishDate', '')
    nt = item.get('_nt', '')
    vendor_key = {'SELECTION_RESULTS': 'vendors', 'CANDIDATE_PUBLICITY': 'candidates', 'PROCUREMENT': 'max_price'}.get(nt, 'vendors')
    vs = '、'.join(item.get('vendors', item.get('candidates', item.get('max_price', ['-'])))) if isinstance(item.get(vendor_key), list) else item.get(vendor_key, '-')
    atype = {'SELECTION_RESULTS': '中选结果', 'CANDIDATE_PUBLICITY': '候选人公示', 'PROCUREMENT': '采购公告'}.get(nt, nt)
    link = f"https://b2b.10086.cn/#/noticeDetail?publishId={item['id']}&publishUuid={item['uuid']}&publishType=PROCUREMENT&publishOneType={nt}"

    fname = f"{idx:02d}_{sanitize_filename(title)}.md"
    fpath = os.path.join(day_dir, fname)

    body = detail_text if detail_text else "无PDF内容"

    md = f"""# {title}

- **单位:** {unit}
- **类型:** {atype}
- **时间:** {ct}
- **厂商/候选人/价格:** {vs}
- **详情链接:** {link}

---

## 公告正文 (PDF提取)

{body}
"""
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"  \U0001f4be 保存: {fpath}", file=sys.stderr)


def escape_md_v2(text):
    """转义markdown_v2特殊字符（用于表格单元格内容）"""
    for ch in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, f'\\{ch}')
    return text


def send_wecom(msg):
    if not WECOM_WEBHOOK:
        print(f"[警告] 未配置 CMCC_WEBHOOK 环境变量，输出到stdout")
    # 始终输出到stdout（当前会话显示）
    print(f"[日报内容]\n{msg}")
    if not WECOM_WEBHOOK:
        return
    # 智能分片：总量 < 4096 字节直接发送，不拆分
    total_bytes = len(msg.encode('utf-8'))
    if total_bytes <= 4096:
        payload = {"msgtype":"markdown_v2","markdown_v2":{"content":msg}}
        proc = subprocess.Popen(['curl','-sX','POST',WECOM_WEBHOOK,
                                '-H','Content-Type: application/json',
                                '-d',json.dumps(payload,ensure_ascii=False)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, _ = proc.communicate(timeout=15)
        print(f"[WeCom] 1/1片: {stdout.decode('utf-8', errors='ignore')[:100]}")
        return
    # 按 section 分片，单 section 超限时回退按行拆
    max_bytes = 3800
    sections = re.split(r'\n(?=\*\*)', msg)
    header_part = sections[0]
    body_sections = sections[1:]
    
    chunks = []
    current = header_part
    for sec in body_sections:
        test = current + '\n' + sec
        if len(test.encode('utf-8')) > max_bytes:
            if current.strip():
                chunks.append(current.rstrip())
            # 单 section 超限 → 按子 section 再拆（每个子标题有自己表头）
            if len(sec.encode('utf-8')) > max_bytes:
                sub_secs = re.split(r'\n(?=\*\*)', sec)
                for ss in sub_secs:
                    if not ss.strip():
                        continue
                    t = current + '\n' + ss if current.strip() else ss
                    if len(t.encode('utf-8')) > max_bytes:
                        if current.strip():
                            chunks.append(current.rstrip())
                        if len(ss.encode('utf-8')) > max_bytes:
                            lines = ss.split('\n')
                            table_header, rest_lines, found = [], [], False
                            for ln in lines:
                                if not found and ln.startswith('|'):
                                    table_header.append(ln)
                                    if len(table_header) >= 2:
                                        found = True
                                elif found:
                                    rest_lines.append(ln)
                                else:
                                    table_header.append(ln)
                            sub_lines = table_header + rest_lines
                            sub = ''
                            # 完整表头：section标题 + 列头 + 分隔行（需重建时携带）
                            full_header = table_header[:]
                            if rest_lines and rest_lines[0].startswith('|'):
                                full_header.append(rest_lines[0])
                            for ln in sub_lines:
                                t2 = sub + '\n' + ln if sub else ln
                                if len(t2.encode('utf-8')) > max_bytes:
                                    if sub.strip():
                                        chunks.append(sub.rstrip())
                                    # 重建表头：确保第二片以后每个子片都有完整表头（含|---|分隔行）
                                    sub = '\n'.join(full_header) + '\n' + ln
                                else:
                                    sub = t2
                            current = sub if sub.strip() else ''
                        else:
                            current = ss
                    else:
                        current = t
            else:
                current = sec
        else:
            current = test
    if current.strip():
        chunks.append(current.rstrip())
    
    for i, chunk in enumerate(chunks):
        payload = {"msgtype":"markdown_v2","markdown_v2":{"content":chunk[:4096]}}
        proc = subprocess.Popen(['curl','-sX','POST',WECOM_WEBHOOK,
                                '-H','Content-Type: application/json',
                                '-d',json.dumps(payload,ensure_ascii=False)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, _ = proc.communicate(timeout=15)
        print(f"[WeCom] 第{i+1}/{len(chunks)}片: {stdout.decode('utf-8', errors='ignore')[:100]}")


def q(nt, kw, page=1):
    d = curl_post(API_LIST, {"current":page,"size":50,"publishOneType":nt,"name":kw})
    return d.get('data',{}).get('content',[]), d.get('data',{}).get('totalElements',0)


def get_text(item):
    payload = {"publishId":item['id'],"publishUuid":item['uuid'],
               "publishType":"PROCUREMENT","publishOneType":item['_nt']}
    for _ in range(2):
        try:
            d = curl_post(API_DETAIL, payload, timeout=45)
            if d.get('code')==0:
                nc = d.get('data',{}).get('noticeContent','')
                if nc:
                    pdf = base64.b64decode(nc)
                    with tempfile.NamedTemporaryFile(suffix='.pdf',delete=False) as f:
                        f.write(pdf); p=f.name
                    proc2 = subprocess.Popen(['/home/linuxbrew/.linuxbrew/bin/python3','-c',f'from pypdf import PdfReader\nfor pg in PdfReader(\"{p}\").pages:\n print(pg.extract_text())'],
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    stdout2, _ = proc2.communicate(timeout=30)
                    os.unlink(p)
                    return re.sub(r'(?<!\n)\n(?!\n)','',stdout2.decode('utf-8', errors='ignore'))
        except: pass
    return ''


def extract_vendors(text):
    res = []
    # 匹配: 标包N的中选/成交人：公司名 或 标包N-名称的中选/成交人：1.公司名
    for m in re.finditer(r'标包(\d+)(?:-[^\s：:]+)?(?:的)?(?:中标|中选|成交)人[：:\s]*(?:\d+[\.、]?\s*)?([^；\n。采购标]+)', text):
        v = re.split(r'采购人|招标代理|20\d{2}', m.group(2))[0].strip().rstrip('。，,. ')
        if v and len(v) > 1: res.append(f"标包{m.group(1)}: {v}")
    if not res:
        # 兜底: 中选/成交人：公司名
        for m in re.finditer(r'(?:中标|中选|成交)人[：:\s]*(?:\d+[\.、]?\s*)?([^；\n。采购标]+)', text):
            v = re.split(r'采购人|招标代理|20\d{2}', m.group(1))[0].strip().rstrip('。，,. ')
            if v and 2<len(v)<80: res.append(v)
    return res or ['-']


def extract_candidates(text):
    """候选人公示 → 候选人列表（支持多种格式）"""
    res = []
    
    # 格式1: 标包N 第X名 公司名...（匹配所有名次）
    for m in re.finditer(r'标包(\d+)(?:名称)?\s*(?:第[一二三四五六七八九十\d]+名)\s*([^\d\n]{4,60}?)(?:未含税|不含税|含税|\d|$)', text):
        pkg, name = m.group(1), m.group(2).strip()
        if name and 3 < len(name) < 60:
            candidate = f"标包{pkg}: {name}"
            if candidate not in res:
                res.append(candidate)
    
    # 格式2: 第X名 公司名...（无标包号或同一标包内第2名之后）
    # 始终执行，不设 if not res 守卫
    for m in re.finditer(r'(?:第[一二三四五六七八九十\d]+名)\s*([^\d\n]{4,60}?)(?:未含税|不含税|含税|\d|$)', text):
        name = m.group(1).strip()
        if name and 3 < len(name) < 60 and name not in res:
            res.append(name)
    
    # 格式3: 清理 / 噪音 + 去重（按公司名核心部分去重）
    cleaned = []
    for r in res:
        r = re.sub(r'\s*/\s*满足.*$', '', r).strip()
        r = re.sub(r'\s*/\s*采购包.*$', '', r).strip()
        # 去重：检查是否已有相同公司名（去除标包前缀后比较）
        core = re.sub(r'^标包\d+:?\s*', '', r)
        if r and not any(re.sub(r'^标包\d+:?\s*', '', c) == core for c in cleaned):
            cleaned.append(r)
    
    return cleaned[:5] or ['-']


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


def make_link(item, nt):
    return f"https://b2b.10086.cn/#/noticeDetail?publishId={item['id']}&publishUuid={item['uuid']}&publishType=PROCUREMENT&publishOneType={nt}"


def main():
    cutoff = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    print(f"查询范围: {cutoff} ~ 今日")

    items = {}
    for nt in ['PROCUREMENT','SELECTION_RESULTS','CANDIDATE_PUBLICITY']:
        for kw in SEC_KW:
            for pg in [1]:
                content, _ = q(nt, kw, pg)
                if not content: break
                for i in content:
                    uid = i.get('uuid')
                    if uid and uid not in items and not any(k in i.get('name','') for k in EXC):
                        items[uid] = {**i, '_nt': nt}
                if len(content)<50: break

    recent = sorted([i for i in items.values() if i.get('publishDate','')[:10]>=cutoff],
                    key=lambda x: x.get('publishDate',''), reverse=True)

    sel = [i for i in recent if i['_nt']=='SELECTION_RESULTS']
    proc = [i for i in recent if i['_nt']=='PROCUREMENT']
    cand = [i for i in recent if i['_nt']=='CANDIDATE_PUBLICITY']

    print(f"中选结果{len(sel)} | 采购公告{len(proc)} | 候选人{len(cand)}")

    for i, item in enumerate(sel, 1):
        txt = get_text(item)
        item['vendors'] = extract_vendors(txt)
        save_to_md(item, txt, 'sel', i, len(sel))
    for i, item in enumerate(cand, 1):
        txt = get_text(item)
        item['candidates'] = extract_candidates(txt)
        save_to_md(item, txt, 'cand', i, len(cand))
    for i, item in enumerate(proc, 1):
        txt = get_text(item)
        item['max_price'] = extract_price(txt)
        save_to_md(item, txt, 'proc', i, len(proc))

    lines = [f"## 📡 中国移动标讯日报",
             f"**数据范围:** {cutoff} ~ 今日 | **来源:** b2b.10086.cn\n"]

    def clean_name(raw, suffixes=None):
        """清理项目名：去特殊字符、后缀、截断"""
        n = raw or ''
        n = re.sub(r'[\n\r\t\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]', '', n)
        if suffixes:
            for s in suffixes:
                n = n.replace(s, '')
        return escape_md_v2(n.strip()[:30])

    if sel:
        lines.append(f"**📊 中选结果公示（{len(sel)}条）**\n")
        lines.append("| 序号 | 项目名称 | 单位 | 中标厂商 | 详情 |")
        lines.append("|------|----------|------|----------|------|")
        for i, item in enumerate(sel, 1):
            name = clean_name(item.get('name',''), ['_中选结果公示','_中标结果公示'])
            unit = escape_md_v2(item.get('companyTypeName',''))
            vs = escape_md_v2('、'.join(item.get('vendors',['-'])[:2]))
            link = make_link(item,'SELECTION_RESULTS')
            lines.append(f"| {i} | {name} | {unit} | {vs} | [详情]({link}) |")
        lines.append("")

    if proc:
        lines.append(f"**📊 采购公告（{len(proc)}条）**\n")
        lines.append("| 序号 | 项目名称 | 单位 | 最高限价/预算 | 详情 |")
        lines.append("|------|----------|------|--------------|------|")
        for i, item in enumerate(proc, 1):
            name = clean_name(item.get('name',''), ['_询比公告','_招标公告','_单一来源公告','_竞争性谈判公告'])
            unit = escape_md_v2(item.get('companyTypeName',''))
            price = escape_md_v2(item.get('max_price','-')[:20])
            link = make_link(item,'PROCUREMENT')
            lines.append(f"| {i} | {name} | {unit} | {price} | [详情]({link}) |")
        lines.append("")

    if cand:
        lines.append(f"**📊 候选人公示（{len(cand)}条）**\n")
        lines.append("| 序号 | 项目名称 | 单位 | 候选人 | 详情 |")
        lines.append("|------|----------|------|--------|------|")
        for i, item in enumerate(cand, 1):
            name = clean_name(item.get('name',''), ['_中选候选人公示','_候选人公示','_中标候选人公示'])
            unit = escape_md_v2(item.get('companyTypeName',''))
            cs = escape_md_v2('、'.join(item.get('candidates',['-'])[:3]))
            link = make_link(item,'CANDIDATE_PUBLICITY')
            lines.append(f"| {i} | {name} | {unit} | {cs} | [详情]({link}) |")
        lines.append("")

    lines.append(f"---\n合计: 中选结果{len(sel)}条 | 采购公告{len(proc)}条 | 候选人公示{len(cand)}条")

    # 保存结构化数据供广东日报使用
    today = datetime.now().strftime("%Y-%m-%d")
    json_dir = os.path.join(SAVE_DIR, today)
    os.makedirs(json_dir, exist_ok=True)
    json_items = []
    for item in sel:
            json_items.append({'title': item.get('name',''), 'unit': item.get('companyTypeName',''), 
                              'vendors': item.get('vendors',[]), 'date': item.get('publishDate',''),
                              'type': '中选结果', 'id': item.get('id',''), 'uuid': item.get('uuid','')})
    for item in proc:
            json_items.append({'title': item.get('name',''), 'unit': item.get('companyTypeName',''),
                              'price': item.get('max_price','-'), 'date': item.get('publishDate',''),
                              'type': '采购公告', 'id': item.get('id',''), 'uuid': item.get('uuid','')})
    for item in cand:
            json_items.append({'title': item.get('name',''), 'unit': item.get('companyTypeName',''),
                              'candidates': item.get('candidates',[]), 'date': item.get('publishDate',''),
                              'type': '候选人公示', 'id': item.get('id',''), 'uuid': item.get('uuid','')})
    with open(os.path.join(json_dir, '_items.json'), 'w', encoding='utf-8') as f:
            json.dump(json_items, f, ensure_ascii=False, indent=2)
    send_wecom('\n'.join(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())
