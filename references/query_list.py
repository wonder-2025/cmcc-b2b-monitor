#!/usr/bin/env python3
"""中国移动B2B采购网 — 公告列表查询"""

import subprocess
import json
import sys
from datetime import datetime, timedelta

API_URL = 'https://b2b.10086.cn/api-b2b/api-sync-es/white_list_api/b2b/publish/queryList'

HEADERS = [
    '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    '-H', 'Content-Type: application/json',
    '-H', 'Accept: application/json',
    '-H', 'Referer: https://b2b.10086.cn/',
]

NOTICE_TYPES = {
    'PROCUREMENT': '采购公告',
    'SELECTION_RESULTS': '中选结果公示',
    'CANDIDATE_PUBLICITY': '中选候选人公示',
    'ONE_SOURCE_PROCUREMENT': '单一来源采购',
    'PREQUALIFICATION': '资格预审',
}

SEC_KEYWORDS = [
    '网络安全', '安全防护', '安全服务', '安全产品', '安全评估', '安全检查',
    '信息安全', '数据安全', '态势感知', '威胁情报', '漏洞管理', '攻防演习',
    '等保测评', '安全培训', '安全隔离', '应用安全', '安全运营', '反诈',
    '暴露面', '合规审计', '安全管控', '密评', '安全认证', '网安',
]

EXCLUDE_KEYWORDS = ['视频监控', '防雷检测', '电力监控', '碳减排', '安全生产']


def query_list(notice_type='PROCUREMENT', keyword=None, page=1, size=50,
               date_start=None, date_end=None):
    """查询公告列表"""
    payload = {
        'current': page,
        'size': size,
        'publishOneType': notice_type,
    }
    if keyword:
        payload['name'] = keyword
    if date_start:
        payload['creationDateStart'] = date_start
    if date_end:
        payload['creationDateEnd'] = date_end

    result = subprocess.run(
        ['curl', '-sX', 'POST', API_URL] + HEADERS + ['-d', json.dumps(payload)],
        capture_output=True, text=True, timeout=30
    )
    data = json.loads(result.stdout)
    if data.get('code') != 0:
        return [], 0
    return data.get('data', {}).get('content', []), data.get('data', {}).get('totalElements', 0)


def query_all_pages(notice_type='PROCUREMENT', keyword=None, max_pages=200):
    """拉取全部分页"""
    all_items = []
    page = 1
    while page <= max_pages:
        items, total = query_list(notice_type=notice_type, keyword=keyword, page=page)
        all_items.extend(items)
        if len(items) < 50 or len(all_items) >= total:
            break
        page += 1
    return all_items


def filter_security(items, exclude=None):
    """过滤网络安全相关公告"""
    exclude = exclude or EXCLUDE_KEYWORDS
    result = []
    seen = set()
    for item in items:
        name = item.get('name', '')
        if any(kw in name for kw in SEC_KEYWORDS) and not any(kw in name for kw in exclude):
            uid = item.get('uuid')
            if uid not in seen:
                seen.add(uid)
                result.append(item)
    return result


def make_link(item, notice_type='PROCUREMENT'):
    """生成详情页链接"""
    base = 'https://b2b.10086.cn/#/noticeDetail'
    return (f"{base}?publishId={item['id']}"
            f"&publishUuid={item['uuid']}"
            f"&publishType=PROCUREMENT"
            f"&publishOneType={notice_type}")


if __name__ == '__main__':
    # 默认查询近7天采购公告
    now = datetime.now()
    date_end = now.strftime('%Y-%m-%d 23:59:59')
    date_start = (now - timedelta(days=7)).strftime('%Y-%m-%d 00:00:00')

    print(f'查询范围: {date_start} ~ {date_end}')
    items = query_all_pages('PROCUREMENT')
    print(f'共 {len(items)} 条采购公告')

    sec_items = filter_security(items)
    print(f'安全相关: {len(sec_items)} 条\n')

    for i, item in enumerate(sec_items, 1):
        print(f'{i}. [{item.get("companyTypeName", "-")}] {item.get("name")}')
        print(f'   发布: {item.get("publishDate")} | {make_link(item)}')
