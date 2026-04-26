#!/usr/bin/env python3
"""中国移动B2B采购网 — 实时标讯监控（指定关键词+公告类型）"""

import subprocess, json, base64, tempfile, os, re, sys
from datetime import datetime

# ===== 配置 =====
WECOM_WEBHOOK = os.environ.get('CMCC_WEBHOOK', '')
STATE_FILE = os.environ.get('CMCC_STATE_FILE', '/tmp/cmcc_monitor_state.json')

API_LIST = 'https://b2b.10086.cn/api-b2b/api-sync-es/white_list_api/b2b/publish/queryList'
API_DETAIL = 'https://b2b.10086.cn/api-b2b/api-sync-es/white_list_api/b2b/publish/queryDetail'
H = ['-H','User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
     '-H','Content-Type: application/json','-H','Accept: application/json','-H','Referer: https://b2b.10086.cn/']

EXC = ['视频监控','防雷检测','电力监控','碳减排','安全生产','碳中和','交通安全','道路交通']


def curl_post(url, payload, timeout=30):
    r = subprocess.run(['curl','-sX','POST',url]+H+['-d',json.dumps(payload)],capture_output=True,text=True,timeout=timeout)
    try: return json.loads(r.stdout)
    except: return {}


def send_wecom(msg):
    if not WECOM_WEBHOOK:
        print(f"[警告] 未配置 CMCC_WEBHOOK 环境变量，跳过推送")
        print(msg)
        return
    max_bytes = 3800
    chunks = []
    current = ''
    for line in msg.split('\n'):
        test = current + '\n' + line if current else line
        if test.encode('utf-8').__len__() > max_bytes:
            if current:
                chunks.append(current)
            current = line
        else:
            current = test
    if current:
        chunks.append(current)
    for i, chunk in enumerate(chunks):
        payload = {"msgtype":"markdown","markdown":{"content":chunk[:4096]}}
        r = subprocess.run(['curl','-sX','POST',WECOM_WEBHOOK,
                            '-H','Content-Type: application/json',
                            '-d',json.dumps(payload,ensure_ascii=False)],
                           capture_output=True,text=True,timeout=15)
        print(f"[WeCom] {r.stdout[:100]}")


def load_state():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except: return {}

def save_state(state):
    with open(STATE_FILE,'w') as f: json.dump(state, f, ensure_ascii=False)


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
                    r2 = subprocess.run(['python3','-c',f'from pypdf import PdfReader\nfor pg in PdfReader("{p}").pages:\n print(pg.extract_text())'],capture_output=True,text=True,timeout=30)
                    os.unlink(p)
                    return re.sub(r'(?<!\n)\n(?!\n)','',r2.stdout)
        except: pass
    return ''


def extract_vendors(text):
    res = []
    for m in re.finditer(r'标包(\d+)(?:的)?(?:中标|中选)人[：:\s]*(?:\d+[\.、]?\s*)([^\n。，标包]+)', text):
        v = re.split(r'采购人|招标代理|标包|20\d{2}', m.group(2))[0].strip().rstrip('。，,. ')
        if v: res.append(f"标包{m.group(1)}: {v}")
    if not res:
        for m in re.finditer(r'(?:中标|中选)人[：:\s]*(?:\d+[\.、]?\s*)([^\n。，]+)', text):
            v = re.split(r'采购人|招标代理|20\d{2}', m.group(1))[0].strip().rstrip('。，,. ')
            if v and 3<len(v)<80: res.append(v)
    return res or ['-']


def extract_candidates(text):
    res = []
    for m in re.finditer(r'标包(\d+)(?:名称)?\s*(?:第[一二三四五六七八九十1234567890]+名|[1-9]\.)\s*([^\d\n]{4,40}?)(?:\s+\d)', text):
        res.append(f"标包{m.group(1)}: {m.group(2).strip()}")
    if not res:
        for m in re.finditer(r'(?:第[一二三四五六七八九十1234567890]+名|[1-9]\.)\s*([^\d\n]{4,40}?)(?:\s+\d)', text):
            n = m.group(1).strip()
            if n not in res: res.append(n)
    return res[:5] or ['-']


def make_link(item):
    return f"https://b2b.10086.cn/#/noticeDetail?publishId={item['id']}&publishUuid={item['uuid']}&publishType=PROCUREMENT&publishOneType={item['_nt']}"


def monitor(keywords, notice_types):
    """监控指定关键词+公告类型的新公告"""
    state = load_state()
    seen_ids = set(state.get('seen_ids', []))
    now_str = datetime.now().strftime('%H:%M:%S')

    print(f"[{now_str}] 监控关键词: {keywords} | 类型: {notice_types}")
    print(f"  已见过: {len(seen_ids)} 条")

    new_items = []
    seen_new = set()
    for kw in keywords:
        for nt in notice_types:
            for pg in [1,2]:
                d = curl_post(API_LIST, {"current":pg,"size":50,"publishOneType":nt,"name":kw})
                content = d.get('data',{}).get('content',[])
                if not content: break
                for item in content:
                    uid = item.get('uuid','')
                    if uid and uid not in seen_ids and uid not in seen_new:
                        if any(k in item.get('name','') for k in EXC): continue
                        seen_ids.add(uid)
                        seen_new.add(uid)
                        item['_nt'] = nt
                        item['_kw'] = kw
                        new_items.append(item)
                if len(content)<50: break

    is_first_run = state.get('last_check', '') == ''
    if is_first_run:
        # 首次运行：推送汇总通知，然后建状态
        if new_items:
            summary_lines = [f"## 🔔 标讯监控初始化", f"**监控关键词:** {', '.join(keywords)}", f"**监控类型:** 中选结果/候选人公示", f"**首次推送历史公告汇总：**\n"]
            for kw in keywords:
                kw_items = [i for i in new_items if i.get('_kw')==kw]
                if kw_items:
                    summary_lines.append(f"**📌 关键词「{kw}」({len(kw_items)}条，展示最近5条)**")
                    for item in kw_items[:5]:
                        name = item.get('name','')[:40]
                        date = item.get('publishDate','')[5:10]
                        prov = item.get('companyTypeName','')
                        nt_label = {'SELECTION_RESULTS':'中标','CANDIDATE_PUBLICITY':'候选'}.get(item['_nt'],'')
                        link = f"https://b2b.10086.cn/#/noticeDetail?publishId={item['id']}&publishUuid={item['uuid']}&publishType=PROCUREMENT&publishOneType={item['_nt']}"
                        summary_lines.append(f"- [{date}] {prov} | {name} ({nt_label}) [查看]({link})")
                    summary_lines.append("")
            summary_lines.append(f"---\n共记录 {len(seen_ids)} 条历史数据，后续仅推送新增公告")
            send_wecom('\n'.join(summary_lines))
            print(f"  首次运行，已推送汇总并记录 {len(seen_ids)} 条到状态库")

        state['seen_ids'] = list(seen_ids)[-3000:]
        state['last_check'] = datetime.now().isoformat()
        state['keywords'] = keywords
        state['notice_types'] = notice_types
        save_state(state)
        return True if new_items else False

    # 更新状态
    state['seen_ids'] = list(seen_ids)[-3000:]
    state['last_check'] = datetime.now().isoformat()
    state['keywords'] = keywords
    state['notice_types'] = notice_types
    save_state(state)

    if not new_items:
        print(f"  无新公告")
        return False

    print(f"  发现 {len(new_items)} 条新公告!")

    for item in new_items[:10]:
        name = item.get('name','')
        date = item.get('publishDate','')
        prov = item.get('companyTypeName','')
        nt = item['_nt']
        kw = item['_kw']

        text = get_text(item)

        nt_label = {'SELECTION_RESULTS':'中选结果公示','CANDIDATE_PUBLICITY':'候选人公示'}.get(nt, nt)

        msg = f"## 🔔 标讯监控提醒\n"
        msg += f"**触发关键词:** {kw}\n"
        msg += f"**公告类型:** {nt_label}\n"
        msg += f"**项目:** {name}\n"
        msg += f"**单位:** {prov} | **发布:** {date}\n"

        if nt == 'SELECTION_RESULTS':
            vs = extract_vendors(text)
            msg += f"**中标厂商:** {' | '.join(vs[:3])}\n"
        elif nt == 'CANDIDATE_PUBLICITY':
            cs = extract_candidates(text)
            msg += f"**候选人:** {' | '.join(cs[:3])}\n"

        msg += f"\n[查看详情]({make_link(item)})"

        send_wecom(msg)
        print(f"  ✓ 已推送: {name[:40]}")

    return True


def main():
    """
    用法: python3 realtime_monitor.py --keywords 广东,威胁 --types SELECTION_RESULTS,CANDIDATE_PUBLICITY
    """
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--keywords', default='广东,威胁', help='监控关键词，逗号分隔')
    parser.add_argument('--types', default='SELECTION_RESULTS,CANDIDATE_PUBLICITY', help='公告类型，逗号分隔')
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(',') if k.strip()]
    types = [t.strip() for t in args.types.split(',') if t.strip()]

    monitor(keywords, types)


if __name__ == '__main__':
    main()
